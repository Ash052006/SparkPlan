from __future__ import annotations

from pathlib import Path

from sparkplan.firmware.compile import _firmware_outputs


def test_firmware_outputs_prefers_sketch_ino_bin(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "boot_app0.bin").write_bytes(b"x" * 8192)
    (build_dir / "sketch.ino.bootloader.bin").write_bytes(b"b" * 1000)
    (build_dir / "sketch.ino.partitions.bin").write_bytes(b"p" * 1000)
    app = build_dir / "sketch.ino.bin"
    app.write_bytes(b"a" * 266000)
    elf = build_dir / "sketch.ino.elf"
    elf.write_bytes(b"e" * 1000)
    bin_path, elf_path = _firmware_outputs(build_dir)
    assert bin_path == app
    assert elf_path == elf


def test_firmware_outputs_falls_back_to_largest_app_bin(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "boot_app0.bin").write_bytes(b"x" * 8192)
    (build_dir / "sketch.ino.bootloader.bin").write_bytes(b"b" * 1000)
    small = build_dir / "other.bin"
    small.write_bytes(b"s" * 50000)
    large = build_dir / "sketch.bin"
    large.write_bytes(b"l" * 200000)
    bin_path, _ = _firmware_outputs(build_dir)
    assert bin_path == large
