# The ypotenuse Compiler - Self Host Variant

<p align="center">
  <img src="assets/logo.png" alt="C△ Logo"/>
</p>

---

## This is the self-hosted version

This variant of the compiler will contain more features, which also means more bugs...

---

## Supported Architectures

The C△ compiler and standard library support multiple architectures:

| Architecture | Platform | Format   | Status  |
| ------------ | -------- | -------- | ------- |
| **x86_64**   | Linux    | ELF64    | Partial |

---

### Standard Library (plstd) Architecture Support

- **printd.plib** — Type-aware printing with conditional compilation for both x86_64 Linux and ARM64 macOS
- **string.plib** — String operations (strcmp, strcpy, strcat, etc.) optimized for each architecture
- **streamer.plib** — File I/O and syscall wrappers supporting both architectures
- **Syscalls** — All syscalls use platform-specific conventions:
  - x86_64: Linux syscall ABI (`mov rax, N; syscall`)

---

## Install Guide

1. Make sure you have **Linux or MacOS** and **Python 3.14+**
2. Clone the repo and `cd` into it
3. Run `make full-install`

```bash
git clone https://github.com/MaimingyouRnlwkey/Hypotenuse-in-C-Triangle
cd The-Hypotenuse-Compiler
make full-install
```

To build the compiler as a Linux x86_64 ELF binary from an ARM64 host, install Docker and run:

```bash
make build-x86_64-elf
make full-install-x86_64-elf
```

---

## Documentation

Full language and compiler documentation lives in the [`docs/`](docs/) folder.

Detailed online documentation is available at:
<https://hypotenuse.mintlify.app>

---

## Contributing

See [`contributing.md`](contributing.md) to get involved!
