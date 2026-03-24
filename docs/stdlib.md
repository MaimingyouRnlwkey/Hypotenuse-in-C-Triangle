# 📦 C△ Standard Library (plstd)

<p align="center">
  <img src="../assets/logo.jpg" alt="C△ Logo" width="120"/>
</p>

**plstd** is the C△ standard library. It is implemented in C△ itself, with `asm` blocks only where syscalls or low-level primitives are needed. plstd is a **single flat library** — not modular. 📚

---

## 📥 Importing plstd

```c
// Globalize all of plstd 🌍
show plstd

// Globalize one module
show lib:io

// Explicit access without globalizing
lib:printd(42);
```

> 💡 The compiler auto-imports what you use — manual imports are optional style.

---

## 🖨️ Output Functions

### `printd(value)` — Type-aware Print

Prints any value, auto-detecting its type. 🔮

```c
printd(42);           // prints: 42
printd("hello");      // prints: hello
printd(3.14);         // prints: 3.14
printd('A');          // prints: A
```

---

### `printfs(format, ...)` — Formatted / f-string Print

Supports `{expr}` f-string interpolation and `%`-style format specifiers. 🎨

```c
string name = "world";
int x = 42;

printfs("Hello, {name}!\n");       // Hello, world!
printfs("x = %d\n", x);           // x = 42
printfs("{x} squared = {x*x}\n"); // 42 squared = 1764
```

---

## 📏 Utility Functions

### `len(collection)` — Length

Returns the length of a string, `dynam` array, or tuple. 📊

```c
string s = "hello";
int l = len(s);   // 5

dynam int nums;
nums.push(1);
nums.push(2);
int n = len(nums);  // 2
```

---

## 🚨 Error Handling

plstd has a built-in error handler with **randomized personality messages** for each error type — contributed by the community via the `errors/` folder. 🎭

```c
// Compiler errors automatically include a personality message
// e.g. "Syntax error on line 7: unexpected '}'"
//      ❌ Oops! You left a brace hanging. Close it up!
```

> 🤝 Want to add your own error personality? See [contributing.md](contributing.md)!

---

## 🔗 plstd Implementation

- Written entirely in **C△** + `asm` blocks for syscalls
- Located in `PLIBS/` system path: `/usr/lib/PLIBS/`
- User libraries: `~/.local/lib/PLIBS/`
- Single flat library — **not modular** by design
