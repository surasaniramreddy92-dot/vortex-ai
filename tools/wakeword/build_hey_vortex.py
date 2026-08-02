"""Builds a custom 'Hey Vortex' wake-word model for openWakeWord.

Pipeline: synthesize many TTS renditions of the phrase (positives) and of
confusable/generic speech (negatives) -> decode to 16kHz PCM -> run through
openWakeWord's shared melspectrogram+embedding preprocessor -> train a small
classifier on 16-frame embedding windows -> export it as an ONNX model with
the exact (1, 16, 96) -> (1, 1) contract openwakeword.Model expects, so it
drops in as a wakeword_models=[...] path exactly like the bundled models.

Two things matter more than they look:
  1. Label quality: a 16-frame window spans 1.28s, almost the whole clip, so
     only windows drawn from the middle of a positive clip actually contain
     the full phrase. Windows near the padded edges get excluded rather than
     mislabeled as positive.
  2. Negative density: real-time streaming evaluates a new window every 80ms,
     so a single false-positive-prone window repeated across a clip's ~20
     overlapping positions will fire eventually. Negatives are sampled at
     stride=1 (dense) to match that, and a hard-negative mining pass re-runs
     the trained model over long concatenated negative streams and folds any
     window that still scores too high back into training.

This is still a fully-synthetic dataset (no recorded speech, no room-noise
augmentation), so treat the result as a working first pass, not a polished
model on par with openWakeWord's officially trained ones.
"""
import asyncio
import hashlib
import os
import subprocess
import sys
import tempfile

import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto
import edge_tts
import imageio_ffmpeg
from sklearn.neural_network import MLPClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from openwakeword.utils import AudioFeatures  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'models')
CACHE_DIR = os.path.join(HERE, 'cache')
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
WINDOW = 16          # frames per prediction window - must match openWakeWord's default
CONCURRENCY = 8
HARD_NEGATIVE_THRESHOLD = 0.15  # any window scoring above this in mining audio gets folded back in as a negative

POSITIVE_PHRASE = 'Hey Vortex.'
VOICES = [
    'en-US-AriaNeural', 'en-US-GuyNeural', 'en-US-JennyNeural', 'en-US-AnaNeural',
    'en-US-AndrewMultilingualNeural', 'en-US-AvaMultilingualNeural', 'en-US-BrianMultilingualNeural',
    'en-US-EmmaMultilingualNeural', 'en-GB-SoniaNeural', 'en-GB-RyanNeural', 'en-AU-NatashaNeural',
    'en-AU-WilliamNeural', 'en-IN-NeerjaNeural', 'en-IN-PrabhatNeural', 'en-CA-ClaraNeural',
    'en-CA-LiamNeural', 'en-IE-EmilyNeural', 'en-ZA-LeahNeural', 'en-NG-EzinneNeural',
]
RATE_VARIANTS = ['-15%', '-8%', '+0%', '+8%', '+15%']
NEGATIVE_VOICES = VOICES[:10]
NEGATIVE_PHRASES = [
    'Hey Jarvis', 'Hey Google', 'Hey Siri', 'Alexa', 'Hey Mycroft', 'Okay Google',
    'Vertex', 'For text', 'War text', 'Vortex', 'Hey', 'Hey there',
    'Open Chrome', "What's the time", 'Close all applications', 'Shut down the system',
    'Explain how Java works', 'Explain how Python works', 'Tell me a joke',
    'Play some music please', 'Turn off the lights',
    'The quick brown fox jumps over the lazy dog', 'I would like a large coffee with milk',
    'Good morning, how are you today', 'Can you check the weather forecast',
    'Hey Vortex is a strange name for a robot', 'A vortex formed in the water',
    'The index of refraction changed', 'Please connect to the wifi network',
]


def cache_path(voice, text, rate):
    key = hashlib.sha1(f'{voice}|{text}|{rate}'.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f'{key}.npy')


def mp3_to_pcm16k(mp3_path):
    proc = subprocess.run(
        [FFMPEG, '-hide_banner', '-loglevel', 'error', '-i', mp3_path,
         '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:1'],
        capture_output=True, check=True,
    )
    audio = np.frombuffer(proc.stdout, dtype=np.int16)
    pad = np.zeros(int(0.4 * 16000), dtype=np.int16)
    return np.concatenate([pad, audio, pad])


async def synth(text, voice, rate='+0%'):
    cpath = cache_path(voice, text, rate)
    if os.path.exists(cpath):
        return np.load(cpath)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
    path = tmp.name
    tmp.close()
    await edge_tts.Communicate(text, voice, rate=rate).save(path)
    try:
        audio = mp3_to_pcm16k(path)
    finally:
        os.remove(path)
    np.save(cpath, audio)
    return audio


async def synth_all(jobs):
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(text, voice, rate):
        async with sem:
            try:
                return await synth(text, voice, rate)
            except Exception as e:
                print(f'  synth failed for ({voice}, {rate!r}, {text!r}): {e}')
                return None

    return await asyncio.gather(*(one(t, v, r) for t, v, r in jobs))


def embed_all_windows(preprocessor, audio):
    """Every valid 16-frame window across the clip, densely (stride 1)."""
    preprocessor.reset()
    step = 1280
    n = len(audio)
    for i in range(0, n - n % step, step):
        preprocessor(audio[i:i + step])
    frames = preprocessor.feature_buffer
    return [frames[j:j + WINDOW] for j in range(0, frames.shape[0] - WINDOW + 1)]


def positive_windows(preprocessor, audio):
    """Only windows drawn from the middle of the clip - full-phrase, not padding."""
    windows = embed_all_windows(preprocessor, audio)
    n = len(windows)
    if n == 0:
        return []
    lo, hi = n // 4, max(n // 4 + 1, (3 * n) // 4)
    return windows[lo:hi]


def train_classifier(X, y):
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=0, stratify=y)
    clf = MLPClassifier(hidden_layer_sizes=(64,), activation='relu', max_iter=2000,
                        random_state=0, early_stopping=True)
    clf.fit(X_train, y_train)
    pred = clf.predict(X_test)
    print(f'  held-out accuracy={accuracy_score(y_test, pred):.3f} '
          f'precision={precision_score(y_test, pred):.3f} recall={recall_score(y_test, pred):.3f}')
    return clf


def build_export_onnx(clf, path):
    w1 = clf.coefs_[0].astype(np.float32)
    b1 = clf.intercepts_[0].astype(np.float32)
    w2 = clf.coefs_[1].astype(np.float32)
    b2 = clf.intercepts_[1].astype(np.float32)

    inp = helper.make_tensor_value_info('input', TensorProto.FLOAT, [1, WINDOW, 96])
    out = helper.make_tensor_value_info('output', TensorProto.FLOAT, [1, 1])
    shape_init = numpy_helper.from_array(np.array([1, WINDOW * 96], dtype=np.int64), name='flat_shape')
    nodes = [
        helper.make_node('Reshape', ['input', 'flat_shape'], ['flat']),
        helper.make_node('Gemm', ['flat', 'w1', 'b1'], ['h1']),
        helper.make_node('Relu', ['h1'], ['h1_relu']),
        helper.make_node('Gemm', ['h1_relu', 'w2', 'b2'], ['logit']),
        helper.make_node('Sigmoid', ['logit'], ['output']),
    ]
    graph = helper.make_graph(
        nodes, 'hey_vortex', [inp], [out],
        initializer=[
            shape_init,
            numpy_helper.from_array(w1, name='w1'), numpy_helper.from_array(b1, name='b1'),
            numpy_helper.from_array(w2, name='w2'), numpy_helper.from_array(b2, name='b2'),
        ],
    )
    model = helper.make_model(graph, producer_name='vortex-custom-wakeword',
                              opset_imports=[helper.make_opsetid('', 15)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, path)
    print(f'Saved {path}')


async def build_dataset(preprocessor):
    print('Synthesizing positive clips ("Hey Vortex")...')
    pos_jobs = [(POSITIVE_PHRASE, v, r) for v in VOICES for r in RATE_VARIANTS]
    pos_audio = [a for a in await synth_all(pos_jobs) if a is not None]
    print(f'  {len(pos_audio)} positive clips')

    print('Synthesizing negative clips (confusables + generic speech)...')
    neg_jobs = [(p, v, '+0%') for v in NEGATIVE_VOICES for p in NEGATIVE_PHRASES]
    neg_audio = [a for a in await synth_all(neg_jobs) if a is not None]
    print(f'  {len(neg_audio)} negative clips')

    print('Extracting embedding windows (dense negatives, trimmed-edge positives)...')
    X, y = [], []
    for audio in pos_audio:
        for w in positive_windows(preprocessor, audio):
            X.append(w.flatten())
            y.append(1)
    for audio in neg_audio:
        for w in embed_all_windows(preprocessor, audio):
            X.append(w.flatten())
            y.append(0)
    rng = np.random.default_rng(0)
    for _ in range(25):
        noise = (rng.normal(0, 300, int(1.6 * 16000))).astype(np.int16)
        for w in embed_all_windows(preprocessor, noise):
            X.append(w.flatten())
            y.append(0)

    return X, y, neg_audio


async def mine_hard_negatives(preprocessor, clf, neg_audio, rounds_seen):
    """Run the current model over long concatenated negative streams (as real
    streaming would see them) and pull back any window that still scores
    too high, so the next training round explicitly corrects it."""
    print(f'Hard-negative mining round {rounds_seen}...')
    rng = np.random.default_rng(rounds_seen)
    hard_X = []
    for _ in range(20):
        chosen = [neg_audio[i] for i in rng.integers(0, len(neg_audio), size=6)]
        stream = np.concatenate(chosen)
        windows = embed_all_windows(preprocessor, stream)
        if not windows:
            continue
        feats = np.array([w.flatten() for w in windows], dtype=np.float32)
        scores = clf.predict_proba(feats)[:, 1]
        for w, s in zip(windows, scores):
            if s > HARD_NEGATIVE_THRESHOLD:
                hard_X.append(w.flatten())
    print(f'  found {len(hard_X)} still-too-confident negative windows')
    return hard_X


async def main():
    preprocessor = AudioFeatures(inference_framework='onnx')
    X, y, neg_audio = await build_dataset(preprocessor)
    X, y = list(X), list(y)
    print(f'  {len(y)} total windows ({sum(y)} positive, {len(y) - sum(y)} negative)')

    print('Training classifier (round 1)...')
    clf = train_classifier(np.array(X, dtype=np.float32), np.array(y, dtype=np.int64))

    for round_ndx in range(1, 3):
        hard_X = await mine_hard_negatives(preprocessor, clf, neg_audio, round_ndx)
        if not hard_X:
            print('  no hard negatives found, stopping mining early')
            break
        X.extend(hard_X)
        y.extend([0] * len(hard_X))
        print(f'Retraining with hard negatives folded in ({len(y)} total windows)...')
        clf = train_classifier(np.array(X, dtype=np.float32), np.array(y, dtype=np.int64))

    onnx_path = os.path.join(OUT_DIR, 'hey_vortex.onnx')
    build_export_onnx(clf, onnx_path)
    return onnx_path


if __name__ == '__main__':
    asyncio.run(main())
