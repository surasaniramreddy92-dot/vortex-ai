"""Clip-level validation of hey_vortex.onnx: does predict_clip's max score cross
a sane threshold for positive utterances, and stay below it for negatives?
Uses voices and phrasings NOT seen during training.
"""
import asyncio
import os
import subprocess
import sys
import tempfile

import numpy as np
import edge_tts
import imageio_ffmpeg

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from openwakeword.model import Model  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
ONNX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models', 'hey_vortex.onnx')

HELDOUT_VOICES = ['en-US-MichelleNeural', 'en-US-SaraNeural', 'en-GB-LibbyNeural',
                  'en-US-AriaNeural', 'en-AU-NatashaNeural']  # last two seen, others not
POSITIVE_TEXTS = ['Hey Vortex.', 'Hey, Vortex!', 'hey vortex']
NEGATIVE_TEXTS = ['Hey Jarvis', 'Hey Google', 'Vertex', 'Vortex', 'Open notepad',
                  'what time is it', 'close all applications', 'explain how python works',
                  'hey there, how are you']


async def synth(text, voice, rate='+0%'):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
    path = tmp.name
    tmp.close()
    await edge_tts.Communicate(text, voice, rate=rate).save(path)
    try:
        proc = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error', '-i', path,
             '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:1'],
            capture_output=True, check=True)
        audio = np.frombuffer(proc.stdout, dtype=np.int16)
        pad = np.zeros(int(0.4 * 16000), dtype=np.int16)
        return np.concatenate([pad, audio, pad])
    finally:
        os.remove(path)


async def main():
    model = Model(wakeword_models=[ONNX_PATH], inference_framework='onnx')
    name = list(model.models.keys())[0]

    print(f'--- positives (want HIGH max score) [model={name}] ---')
    pos_scores = []
    for voice in HELDOUT_VOICES:
        for text in POSITIVE_TEXTS:
            try:
                audio = await synth(text, voice)
            except Exception as e:
                print(f'  {voice:28s} {text!r:20s} SYNTH FAILED: {e}')
                continue
            model.reset()
            preds = model.predict_clip(audio)
            scores = [p[name] for p in preds]
            m = max(scores)
            pos_scores.append(m)
            print(f'  {voice:28s} {text!r:20s} max={m:.3f}')

    print(f'--- negatives (want LOW max score) ---')
    neg_scores = []
    for voice in HELDOUT_VOICES:
        for text in NEGATIVE_TEXTS:
            try:
                audio = await synth(text, voice)
            except Exception as e:
                print(f'  {voice:28s} {text!r:20s} SYNTH FAILED: {e}')
                continue
            model.reset()
            preds = model.predict_clip(audio)
            scores = [p[name] for p in preds]
            m = max(scores)
            neg_scores.append(m)
            print(f'  {voice:28s} {text!r:20s} max={m:.3f}')

    print()
    print(f'positive max scores: min={min(pos_scores):.3f} mean={np.mean(pos_scores):.3f}')
    print(f'negative max scores: max={max(neg_scores):.3f} mean={np.mean(neg_scores):.3f}')
    for thr in [0.3, 0.4, 0.5, 0.6, 0.7]:
        tp = sum(s >= thr for s in pos_scores)
        fp = sum(s >= thr for s in neg_scores)
        print(f'  threshold={thr}: catches {tp}/{len(pos_scores)} positives, '
              f'{fp}/{len(neg_scores)} false positives')


if __name__ == '__main__':
    asyncio.run(main())
