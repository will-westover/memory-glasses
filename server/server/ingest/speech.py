"""Find where people speak in audio and what each voice sounds like, with small local models on the CPU."""

import functools
import subprocess
from pathlib import Path

import numpy as np

from server.config import SERVER_DIR

MODELS_DIR = SERVER_DIR / "models"
VAD_MODEL = MODELS_DIR / "silero_vad.onnx"
VOICE_MODEL = MODELS_DIR / "voxceleb_resnet34_LM.onnx"

SAMPLE_RATE = 16000
VAD_WINDOW = 512
VAD_CONTEXT = 64
WINDOW_SECONDS = VAD_WINDOW / SAMPLE_RATE

SPEECH_THRESHOLD = 0.5
SILENCE_THRESHOLD = 0.35
MIN_SPEECH_SECONDS = 0.25
MIN_PAUSE_SECONDS = 1.0
PAD_SECONDS = 0.2
MAX_CHUNK_SECONDS = 30.0

MEL_BINS = 80
FRAME_SAMPLES = 400
FRAME_SHIFT = 160
FFT_SIZE = 512
MIN_VOICE_SECONDS = 0.5


def load_audio(path: Path) -> np.ndarray:
    """Return the audio as mono 16 kHz float samples between -1 and 1."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    )

    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768


def session(path: Path, threads: int = 1):
    """Open one ONNX model on the CPU."""
    import onnxruntime

    if not path.exists():
        raise SystemExit(f"error: missing {path}. See README.md for where to download it")

    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = threads

    return onnxruntime.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


@functools.cache
def vad_session():
    return session(VAD_MODEL)


@functools.cache
def voice_session():
    return session(VOICE_MODEL, threads=4)


def speech_probabilities(samples: np.ndarray) -> np.ndarray:
    """Return the chance of speech in each 32 ms window of the audio."""
    model = vad_session()
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, VAD_CONTEXT), dtype=np.float32)
    rate = np.array(SAMPLE_RATE, dtype=np.int64)

    windows = len(samples) // VAD_WINDOW
    probabilities = np.zeros(windows, dtype=np.float32)

    for index in range(windows):
        window = samples[index * VAD_WINDOW : (index + 1) * VAD_WINDOW][None, :]
        output, state = model.run(
            None, {"input": np.concatenate([context, window], axis=1), "state": state, "sr": rate}
        )
        context = window[:, -VAD_CONTEXT:]
        probabilities[index] = output[0, 0]

    return probabilities


def speech_regions(probabilities: np.ndarray, window_seconds: float = WINDOW_SECONDS) -> list[tuple[float, float]]:
    """Return (start, end) seconds of speech, joined across pauses shorter than MIN_PAUSE_SECONDS."""
    regions = []
    start = None
    last_speech = None

    for index, probability in enumerate(probabilities):
        at = index * window_seconds

        if probability >= SPEECH_THRESHOLD:
            if start is None:
                start = at
            last_speech = at + window_seconds
        elif start is not None and probability < SILENCE_THRESHOLD and at - last_speech >= MIN_PAUSE_SECONDS:
            regions.append((start, last_speech))
            start = None

    if start is not None:
        regions.append((start, last_speech))

    return [(start, end) for start, end in regions if end - start >= MIN_SPEECH_SECONDS]


def split_long(
    region: tuple[float, float],
    probabilities: np.ndarray,
    window_seconds: float = WINDOW_SECONDS,
    limit: float = MAX_CHUNK_SECONDS,
) -> list[tuple[float, float]]:
    """Split a region longer than the limit at its quietest windows, so each part is at most the limit."""
    start, end = region

    if end - start <= limit:
        return [region]

    first = int(start / window_seconds + 0.5)
    last = int(end / window_seconds + 0.5)
    middle_start = first + int(limit / (2 * window_seconds))
    middle_end = min(last, first + int(limit / window_seconds)) - 1

    if middle_end <= middle_start:
        cut = first + int(limit / window_seconds)
    else:
        cut = middle_start + int(np.argmin(probabilities[middle_start:middle_end]))

    at = cut * window_seconds

    return [(start, at)] + split_long((at, end), probabilities, window_seconds, limit)


def padded(regions: list[tuple[float, float]], duration: float, pad: float = PAD_SECONDS) -> list[tuple[float, float]]:
    """Widen each region by pad on both sides, without passing the audio's ends or a neighbour's midpoint."""
    widened = []

    for index, (start, end) in enumerate(regions):
        before = regions[index - 1][1] if index > 0 else None
        after = regions[index + 1][0] if index + 1 < len(regions) else None

        low = max(0.0, start - pad, (before + start) / 2 if before is not None else 0.0)
        high = min(duration, end + pad, (end + after) / 2 if after is not None else duration)
        widened.append((round(low, 3), round(high, 3)))

    return widened


def speech_chunks(samples: np.ndarray) -> list[tuple[float, float]]:
    """Return (start, end) seconds of each run of speech, padded, at most MAX_CHUNK_SECONDS long."""
    probabilities = speech_probabilities(samples)

    regions = [part for region in speech_regions(probabilities) for part in split_long(region, probabilities)]

    return padded(regions, len(samples) / SAMPLE_RATE)


def clip(samples: np.ndarray, start: float, end: float) -> np.ndarray:
    """Return the samples between two times in seconds."""
    return samples[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]


@functools.cache
def mel_filters() -> np.ndarray:
    """Return Kaldi-style triangular mel filters over the FFT bins."""

    def mel(hz):
        return 1127 * np.log(1 + np.asarray(hz) / 700)

    edges = np.linspace(mel(20), mel(SAMPLE_RATE / 2), MEL_BINS + 2)
    bins = mel(np.arange(FFT_SIZE // 2 + 1) * SAMPLE_RATE / FFT_SIZE)

    left, centre, right = edges[:-2, None], edges[1:-1, None], edges[2:, None]
    rising = (bins - left) / (centre - left)
    falling = (right - bins) / (right - centre)

    return np.clip(np.minimum(rising, falling), 0, None).astype(np.float32)


def fbank(samples: np.ndarray) -> np.ndarray:
    """Return 80 log mel energies per 10 ms, as Kaldi computes them, with the mean removed."""
    scaled = samples.astype(np.float32) * 32768
    count = 1 + (len(scaled) - FRAME_SAMPLES) // FRAME_SHIFT
    frames = np.lib.stride_tricks.sliding_window_view(scaled, FRAME_SAMPLES)[::FRAME_SHIFT][:count].copy()

    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= 0.97 * frames[:, :-1]
    frames[:, 0] -= 0.97 * frames[:, 0]
    frames *= np.hamming(FRAME_SAMPLES).astype(np.float32)

    power = np.abs(np.fft.rfft(frames, FFT_SIZE)) ** 2
    energies = np.log(np.maximum(power @ mel_filters().T, np.finfo(np.float32).eps))

    return (energies - energies.mean(axis=0)).astype(np.float32)


def voice_embedding(samples: np.ndarray) -> list[float] | None:
    """Return a unit-length 256-number voiceprint of one stretch of speech, or None if it is too short."""
    if len(samples) < MIN_VOICE_SECONDS * SAMPLE_RATE:
        return None

    features = fbank(samples)[None, :, :]
    vector = voice_session().run(None, {"feats": features})[0][0]

    return (vector / max(float(np.linalg.norm(vector)), 1e-9)).tolist()
