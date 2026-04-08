"""
OGG → MP3 / M4A 変換スクリプト

【概要】
OGGファイルをNotebookLMに取り込める形式（MP3またはM4A）に変換する。

【使い方】
  python scripts/convert_ogg.py input.ogg               # → input.mp3 を生成
  python scripts/convert_ogg.py input.ogg -f m4a        # → input.m4a を生成
  python scripts/convert_ogg.py *.ogg -o output_dir/    # 複数ファイルを一括変換

【オプション】
  -f, --format    出力形式: mp3 (デフォルト) または m4a
  -o, --output    出力先ディレクトリ (省略時は入力ファイルと同じ場所)
  -b, --bitrate   ビットレート (デフォルト: 128k)
"""

import argparse
import sys
from pathlib import Path
from pydub import AudioSegment


def convert_ogg(input_path: Path, output_format: str, output_dir: Path | None, bitrate: str) -> Path:
    """OGGファイルを指定フォーマットに変換する。"""
    audio = AudioSegment.from_ogg(str(input_path))

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / input_path.with_suffix(f".{output_format}").name
    else:
        output_path = input_path.with_suffix(f".{output_format}")

    export_kwargs = {"bitrate": bitrate}
    if output_format == "m4a":
        export_kwargs["format"] = "ipod"  # pydub では m4a は ipod コーデック
    else:
        export_kwargs["format"] = output_format

    audio.export(str(output_path), **export_kwargs)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="OGGファイルをMP3/M4Aに変換する")
    parser.add_argument("inputs", nargs="+", type=Path, help="変換するOGGファイル")
    parser.add_argument("-f", "--format", choices=["mp3", "m4a"], default="mp3", help="出力形式 (デフォルト: mp3)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="出力先ディレクトリ")
    parser.add_argument("-b", "--bitrate", default="128k", help="ビットレート (デフォルト: 128k)")
    args = parser.parse_args()

    success, failed = 0, 0
    for input_path in args.inputs:
        if not input_path.exists():
            print(f"[ERROR] ファイルが見つかりません: {input_path}", file=sys.stderr)
            failed += 1
            continue
        if input_path.suffix.lower() != ".ogg":
            print(f"[SKIP]  OGGファイルではありません: {input_path}", file=sys.stderr)
            continue
        try:
            output_path = convert_ogg(input_path, args.format, args.output, args.bitrate)
            print(f"[OK]    {input_path} → {output_path}")
            success += 1
        except Exception as e:
            print(f"[ERROR] {input_path}: {e}", file=sys.stderr)
            failed += 1

    print(f"\n完了: {success}件成功, {failed}件失敗")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
