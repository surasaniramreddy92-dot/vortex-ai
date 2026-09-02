"""Generates a small, throwaway FAKE dataset (via the existing edge-tts
integration, not a real recorded voice) purely to prove the training
pipeline itself works - dependencies import, metadata.csv's format is
accepted, phonemization succeeds, and at least one real training step
completes - before asking for real recorded voice data.

This CANNOT and does not produce a usable voice - a handful of synthetic
clips from a commercial cloud voice is nowhere near enough data, and
training "on" them would just teach a model to imitate that cloud voice
budget-quality, at best. Its only purpose is de-risking the dependency/
pipeline setup. Writes to smoke_test_dataset/ (a name deliberately distinct
from record.py's real dataset/ output, so the two are never confused) and
is meant to be deleted after the smoke test - see README.md.
"""
import asyncio
import os

import edge_tts
import librosa
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'smoke_test_dataset')
WAVS_DIR = os.path.join(OUT_DIR, 'wavs')
METADATA_PATH = os.path.join(OUT_DIR, 'metadata.csv')
SAMPLE_RATE = 22050

# A handful of short, distinct throwaway lines - real content doesn't
# matter here, only that phonemization/training accept them.
FAKE_SENTENCES = [
    "This is a smoke test sentence, not real training data.",
    "The quick brown fox jumps over the lazy dog.",
    "Testing one, two, three, four, five.",
    "A short throwaway sentence for pipeline verification.",
    "This clip only exists to prove the pipeline works.",
    "Nothing spoken here is meant to become a real voice.",
]


async def _synth(text, mp3_path):
    await edge_tts.Communicate(text, 'en-US-EmmaMultilingualNeural').save(mp3_path)


def main():
    os.makedirs(WAVS_DIR, exist_ok=True)
    with open(METADATA_PATH, 'w', encoding='utf-8', newline='') as csv_file:
        for i, text in enumerate(FAKE_SENTENCES):
            utt_id = f'smoke_{i:03d}'
            mp3_path = os.path.join(WAVS_DIR, f'{utt_id}.mp3')
            wav_path = os.path.join(WAVS_DIR, f'{utt_id}.wav')
            asyncio.run(_synth(text, mp3_path))
            audio, _ = librosa.load(mp3_path, sr=SAMPLE_RATE, mono=True)
            sf.write(wav_path, audio, SAMPLE_RATE)
            os.remove(mp3_path)
            csv_file.write(f'{utt_id}|{text}\n')
            print(f'  {utt_id}: {text}')
    print(f'\nFake smoke-test dataset ready at {OUT_DIR} ({len(FAKE_SENTENCES)} clips).')
    print('This is NOT real training data - delete this directory after the smoke test.')


if __name__ == '__main__':
    main()
