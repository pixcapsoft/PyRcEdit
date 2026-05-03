# 🛠️ PyRcEdit

[![Build](https://github.com/pixcapsoft/PyRcEdit/actions/workflows/build.yml/badge.svg)](https://github.com/pixcapsoft/PyRcEdit/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/pixcapsoft/PyRcEdit)](https://github.com/pixcapsoft/PyRcEdit/releases)
[![Downloads](https://img.shields.io/github/downloads/pixcapsoft/PyRcEdit/total.svg)](https://github.com/pixcapsoft/PyRcEdit/releases)
[![License](https://img.shields.io/github/license/pixcapsoft/PyRcEdit)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
[![Issues](https://img.shields.io/github/issues/pixcapsoft/PyRcEdit)](https://github.com/pixcapsoft/PyRcEdit/issues)

---

## 📌 Overview

**PyRcEdit** is a modern Python reimplementation of the archived  
[rcedit](https://github.com/electron/rcedit) tool originally developed by GitHub/Electron.

It is a fast, lightweight command-line utility for editing embedded resources inside Windows PE files (`.exe` / `.dll`).  
Unlike the original C++ tool, PyRcEdit is:

- Written in **pure Python**
- Uses **ctypes** to call native Win32 APIs
- Requires **no C++ toolchain** (no Visual Studio, no CMake)
- Easy to integrate into **CI/CD pipelines**

> ⚠️ **Important Notes**  
> • PyRcEdit runs **only on Windows**, as it relies on native Win32 APIs.  
> • Some executables may become corrupted after modification. Always keep a **backup copy** of your file before editing.

---

## ✨ Features

- 📝 Modify Version String attributes (e.g., *File Description*, *Company Name*)  
- 🎨 Replace application icons (`.ico`)  
- 🛡️ Change UAC Execution Levels (`asInvoker`, `requireAdministrator`, etc.)  
- 📄 Inject or update application manifests  
- 🔤 Edit localized string resources (`RT_STRING`)  
- 📦 Embed or replace raw binary data (`RT_RCDATA`)  
- ⚡ **Zero compilation required** — just download the `.exe`, add to PATH, and use it anywhere  

---

## 📦 Installation

### 🔹 Download Executable (Recommended)
Download the latest `PyRcEdit.exe` from the Releases page:

👉 https://github.com/pixcapsoft/PyRcEdit/releases/latest

After downloading:

1. Place the file anywhere you like  
2. Add the folder to your **System PATH**  
3. Run `pyrcedit` from any directory  

### 🔹 Install From Source (Optional)
```bash
git clone https://github.com/pixcapsoft/PyRcEdit.git
cd PyRcEdit
pip install pyinstaller
pyinstaller --onefile --console --name pyrcedit pyrcedit.py
```

---

## 🚀 Quick Start

### Prerequisites
*   Windows OS
*   Python 3.11+ (If you want build from source)

### Basic Usage

Simply run the PyRcEdit with your target executable and the desired flags:

```bash
pyrcedit "path-to-file.exe" [options...]
```

> **Pro Tip:** You can chain multiple options in a single command!
> ```bash
> pyrcedit "E:\MyFile\app.exe" --set-icon "icon.ico" --set-file-version "1.0.0.0"
> ```

> ⚠️ Important: Always provide the full path to the executable.  
> Relative paths may cause:  
> `fatal error : Unable to commit change`

### 📁 Directory Mode (Using pyrcedit.prec)

If you frequently run the same commands, place them in a pyrcedit.prec file at your project folder and run:

```bash
pyrcedit .
```

Example `pyrcedit.prec` contents:
```
"E:\MyFile\app.exe" --set-product-version 1.0.0.0 --set-icon "app.ico"
```

> 👉 This feature is really suitable for developers who want to edit their build files each time they build again.

For example imagine this:
You compiling python application using some compiler that won't let you change the output file's icon. Output file located in `build` folder. You only had to create `pyrcedit.prec` file inside the build folder with following code.

```bash
<Full-Path-To-Your-EXE> --set-icon <Your-Icon>
```

Then after compiling your application simply run:

```bash
pyrcedit .
```
and see the magic...

---

## 📖 Command Reference

### Information / Help
| Command | Description |
|---|---|
| `-h`, `--help` | Show the help message and exit. |
| `-v`, `--version` | Print current version of the PyRcEdit |
| `--repo` | Get the official repo link and credits |

### Version Information
| Command | Description | Example |
|---|---|---|
| `--set-version-string <key> <value>` | Set a specific version string property. | `--set-version-string "CompanyName" "MyCompany"` |
| `--get-version-string <key>` | Print a specific version string. | `--get-version-string "CompanyName"` |
| `--set-file-version <version>` | Set the `FileVersion` attribute. | `--set-file-version "1.2.3.4"` |
| `--set-product-version <version>` | Set the `ProductVersion` attribute. | `--set-product-version "1.2.3.4"` |

### Visuals & Icons
| Command | Description | Example |
|---|---|---|
| `--set-icon <path-to-ico>` | Replace the executable's `.ico` file. | `--set-icon "./assets/app.ico"` |

### System & Security
| Command | Description | Example |
|---|---|---|
| `--set-requested-execution-level <level>` | Set UAC level in manifest. Valid options: `asInvoker`, `highestAvailable`, `requireAdministrator`. | `--set-requested-execution-level "requireAdministrator"` |
| `--application-manifest <path-to-file>` | Set application manifest from an external XML file. | `--application-manifest "./app.manifest"` |

### Resources (Strings & Raw Data)
| Command | Description | Example |
|---|---|---|
| `--set-resource-string <id> <value>` | Set string resource by numeric ID. | `--set-resource-string 104 "New String"` |
| `--get-resource-string <id>` | Get string resource by numeric ID. | `--get-resource-string 104` |
| `--set-rcdata <id> <path-to-file>` | Replace `RCDATA` resource by numeric ID using binary file contents. | `--set-rcdata 1 "./payload.bin"` |

---

💡 Why PyRcEdit?

The original rcedit tool is archived and requires a full C++ build environment.  
PyRcEdit provides a modern, lightweight alternative:

- Pure Python implementation  
- No compilation required  
- Easy to automate  
- Actively maintained  
- Ideal for CI/CD pipelines on Windows  

---

## ❓ FAQ

**Why do I need to provide a full path to the executable?**
Win32 APIs used internally cannot reliably resolve relative paths.

**Why did my .exe get corrupted?**
Some PE files use non-standard layouts. Always keep a backup.

**Does PyRcEdit work on Linux or macOS?**
Not currently. It relies on native Windows APIs.

---

## 🤝 Contributing

Contributions are always welcome! Please read our [contribution guidelines](CONTRIBUTING.md) first to get started.

---

## 🔒 Security

If you discover a vulnerability, please follow our [security guidelines](SECURITY.md)

---

## 🙏 Credits

*   Original approach and C++ implementation by the [Electron team](https://github.com/electron/rcedit).
*   This Python port brings frictionless, scriptable metadata modification to typical continuous integration (CI) workflows on Windows using Python.
