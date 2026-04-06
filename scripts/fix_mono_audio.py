#!/usr/bin/env python3
"""
BlackHoleなどで録音したモノラル音声が片耳しか聞こえない問題を修正するスクリプト。

原因: BlackHoleで録音すると、音声がステレオファイルの片チャンネル（主に左）にのみ
      記録され、もう一方のチャンネルが無音になるケースがある。

修正: 音声のあるチャンネルをもう一方にコピーして、両耳で聞こえるようにする。
"""

import struct
import sys
import wave
from pathlib import Path


def fix_mono_audio_wave(input_path: Path, output_path: Path) -> None:
    """WAVファイルのモノラル片耳問題を修正する。"""
    with wave.open(str(input_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    print(f"  チャンネル数: {n_channels}")
    print(f"  サンプル幅: {sampwidth} bytes ({sampwidth * 8} bit)")
    print(f"  サンプルレート: {framerate} Hz")
    print(f"  フレーム数: {n_frames}")

    if n_channels == 1:
        # モノラルファイル → ステレオに変換（両チャンネルに同じ音声）
        print("  [修正] モノラル → ステレオ変換")
        fmt = {1: "b", 2: "h", 4: "i"}[sampwidth]
        samples = struct.unpack(f"<{n_frames}{fmt}", raw)
        stereo = []
        for s in samples:
            stereo.extend([s, s])
        out_raw = struct.pack(f"<{len(stereo)}{fmt}", *stereo)

        with wave.open(str(output_path), "wb") as wf_out:
            wf_out.setnchannels(2)
            wf_out.setsampwidth(sampwidth)
            wf_out.setframerate(framerate)
            wf_out.writeframes(out_raw)

    elif n_channels == 2:
        # ステレオファイル → どちらのチャンネルに音声があるか確認
        fmt = {1: "b", 2: "h", 4: "i"}[sampwidth]
        total_samples = n_frames * 2
        samples = struct.unpack(f"<{total_samples}{fmt}", raw)

        left = samples[0::2]
        right = samples[1::2]

        left_energy = sum(abs(s) for s in left)
        right_energy = sum(abs(s) for s in right)

        print(f"  左チャンネル エネルギー: {left_energy}")
        print(f"  右チャンネル エネルギー: {right_energy}")

        if left_energy == 0 and right_energy == 0:
            print("  [警告] 両チャンネルとも無音です")
            output_path.write_bytes(input_path.read_bytes())
            return

        ratio = max(left_energy, right_energy) / (min(left_energy, right_energy) + 1)

        if ratio < 10:
            print("  [スキップ] 両チャンネルに音声があります（修正不要）")
            output_path.write_bytes(input_path.read_bytes())
            return

        if left_energy > right_energy:
            print("  [修正] 左チャンネルの音声を右チャンネルにコピー")
            source = left
        else:
            print("  [修正] 右チャンネルの音声を左チャンネルにコピー")
            source = right

        stereo = []
        for s in source:
            stereo.extend([s, s])
        out_raw = struct.pack(f"<{len(stereo)}{fmt}", *stereo)

        with wave.open(str(output_path), "wb") as wf_out:
            wf_out.setnchannels(2)
            wf_out.setsampwidth(sampwidth)
            wf_out.setframerate(framerate)
            wf_out.writeframes(out_raw)

    else:
        print(f"  [非対応] チャンネル数 {n_channels} は非対応です")
        sys.exit(1)


def fix_audio(input_path: str, output_path: str | None = None) -> None:
    inp = Path(input_path)
    if not inp.exists():
        print(f"エラー: ファイルが見つかりません: {inp}")
        sys.exit(1)

    if output_path is None:
        out = inp.with_name(inp.stem + "_fixed" + inp.suffix)
    else:
        out = Path(output_path)

    print(f"入力: {inp}")
    print(f"出力: {out}")

    suffix = inp.suffix.lower()

    if suffix == ".wav":
        fix_mono_audio_wave(inp, out)
        print(f"完了: {out}")
    else:
        # WAV以外はffmpegが必要
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_file(str(inp))
            print(f"  チャンネル数: {audio.channels}")
            print(f"  サンプルレート: {audio.frame_rate} Hz")

            if audio.channels == 1:
                print("  [修正] モノラル → ステレオ変換")
                stereo = AudioSegment.from_mono_audiosegments(audio, audio)
            elif audio.channels == 2:
                channels = audio.split_to_mono()
                left_energy = channels[0].rms
                right_energy = channels[1].rms

                print(f"  左チャンネル RMS: {left_energy}")
                print(f"  右チャンネル RMS: {right_energy}")

                if left_energy == 0 and right_energy == 0:
                    print("  [警告] 両チャンネルとも無音です")
                    return

                ratio = max(left_energy, right_energy) / (min(left_energy, right_energy) + 1)
                if ratio < 10:
                    print("  [スキップ] 両チャンネルに音声があります（修正不要）")
                    return

                if left_energy > right_energy:
                    print("  [修正] 左チャンネルの音声を右チャンネルにコピー")
                    source = channels[0]
                else:
                    print("  [修正] 右チャンネルの音声を左チャンネルにコピー")
                    source = channels[1]

                stereo = AudioSegment.from_mono_audiosegments(source, source)
            else:
                print(f"  [非対応] チャンネル数 {audio.channels} は非対応です")
                sys.exit(1)

            stereo.export(str(out), format=suffix.lstrip("."))
            print(f"完了: {out}")

        except ImportError:
            print(
                f"エラー: {suffix} ファイルの処理にはffmpegが必要です。\n"
                "  brew install ffmpeg  でインストールしてください。\n"
                "  または .wav に変換してから実行してください。"
            )
            sys.exit(1)
        except Exception as e:
            print(f"エラー: {e}")
            sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print("使い方:")
        print("  python fix_mono_audio.py <入力ファイル> [出力ファイル]")
        print("")
        print("例:")
        print("  python fix_mono_audio.py recording.wav")
        print("  python fix_mono_audio.py recording.wav fixed.wav")
        print("  python fix_mono_audio.py interview.m4a")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    fix_audio(input_file, output_file)


if __name__ == "__main__":
    main()
