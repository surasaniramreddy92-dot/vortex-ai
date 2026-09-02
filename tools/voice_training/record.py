"""Recording helper for tools/voice_training/script.py's sentences.

Run this directly, yourself, in your own terminal:
    python tools/voice_training/record.py

There is no way for Claude to participate in this step live - it has no
microphone access and can't hear you read the sentences. This tool exists
purely to remove the busywork (file naming, keeping the CSV in sync) so you
can focus on reading.

Records at 22050Hz mono (Piper's own default sample_rate - see
piper/train/vits/dataset.py - avoiding a resample step during training),
one WAV per sentence, and appends the matching line to metadata.csv in
Piper's expected LJSpeech-style format (utt_id|text, pipe-delimited).

Resumable: already-recorded sentences (already present in metadata.csv)
are skipped on a re-run, so this can be spread across multiple sessions
instead of one long sitting. To redo a sentence you weren't happy with,
delete its line from metadata.csv and its .wav file from wavs/, then
re-run this script - it will ask for that sentence again.

Recording is variable-length (press Enter to start, press Enter again to
stop), not a fixed timer - sentence lengths in script.py vary a lot
(4-14 words), and a fixed window would either cut long sentences off or
waste time waiting out a timer on short ones.
"""
import csv
import os
import sys
import wave

import numpy as np
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from script import SENTENCES  # noqa: E402

SAMPLE_RATE = 22050
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'dataset')
WAVS_DIR = os.path.join(OUT_DIR, 'wavs')
METADATA_PATH = os.path.join(OUT_DIR, 'metadata.csv')
MIN_DURATION_SECONDS = 0.3  # anything shorter almost certainly means Enter was pressed twice by accident


def _already_recorded():
    if not os.path.exists(METADATA_PATH):
        return set()
    with open(METADATA_PATH, encoding='utf-8') as f:
        return {line.split('|', 1)[0] for line in f if line.strip()}


def _record_one():
    """Blocks until Enter is pressed again; returns the recorded audio as
    one float32 numpy array, shape (num_samples, 1)."""
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', callback=callback)
    with stream:
        input()
    return np.concatenate(frames, axis=0) if frames else np.zeros((0, 1), dtype='float32')


def _save_wav(path, audio):
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())


def main():
    os.makedirs(WAVS_DIR, exist_ok=True)
    done = _already_recorded()
    remaining = [(f'utt_{i:04d}', text) for i, text in enumerate(SENTENCES) if f'utt_{i:04d}' not in done]

    if not remaining:
        print(f'All {len(SENTENCES)} sentences are already recorded in {METADATA_PATH}.')
        print('To redo one, delete its line from metadata.csv and its .wav file from wavs/, then re-run this.')
        return

    print(f'{len(done)} already recorded, {len(remaining)} remaining this session.')
    print('For each sentence: press Enter to start recording, read it aloud, press Enter again to stop.\n')

    with open(METADATA_PATH, 'a', encoding='utf-8', newline='') as csv_file:
        writer = csv.writer(csv_file, delimiter='|')
        for utt_id, text in remaining:
            print(f'\n[{utt_id}] "{text}"')
            input('  Press Enter to start recording...')
            audio = _record_one()
            duration = len(audio) / SAMPLE_RATE
            if duration < MIN_DURATION_SECONDS:
                print(f'  Only {duration:.2f}s recorded - too short, skipped. It will be asked again next run.')
                continue
            _save_wav(os.path.join(WAVS_DIR, f'{utt_id}.wav'), audio)
            writer.writerow([utt_id, text])
            csv_file.flush()
            print(f'  Saved ({duration:.2f}s).')

    print(f'\nSession complete. Dataset so far: {OUT_DIR}')
    print('Run this script again anytime to continue with any sentences you skipped or haven\'t reached yet.')


if __name__ == '__main__':
    main()
