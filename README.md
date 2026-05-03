# 🛠️ PyRcEdit

[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()

**PyRcEdit** is a modern Python reimplementation of the archived [rcedit](https://github.com/electron/rcedit) tool originally developed by GitHub/Electron.

It is a fast, command-line utility used to edit embedded resources of Windows PE files (`.exe` / `.dll`). Written in pure Python and leveraging `ctypes` to call Win32 APIs, PyRcEdit saves you the hassle of setting up a heavy C++ build toolchain (CMake, Visual Studio) that the original tool required.

> **Note:** Because it uses native Win32 APIs for safely modifying the Portable Executable (PE) structure, it currently runs natively on **Windows only**.

---

## ✨ Features
*   📝 Modify Version String attributes (e.g., *File Description*, *Company Name*).
*   🎨 Change Application Icons (`.ico`).
*   🛡️ Modify requested Execution Levels for UAC (`asInvoker`, `requireAdministrator`, etc.).
*   📄 Inject or update Application Manifests natively.
*   🔤 Edit localized Resource Strings (`RT_STRING`).
*   📦 Embed or replace Raw Data blobs (`RT_RCDATA`).
*   ⚡ **Zero compilation required! Just add to PATH** once you download the PyRcEdit.exe you had add it to system PATH and can use it system widely.

---

## 🚀 Quick Start

### Prerequisites
*   Windows OS
*   Python 3.8+

### Basic Usage

Simply run the script with your target executable and the desired flags:

```bash
pyrcedit "path-to-file.exe" [options...]
```

> **Pro Tip:** You can chain multiple options in a single command!
> ```bash
> pyrcedit "app.exe" --set-icon "icon.ico" --set-file-version "1.0.0.0"
> ```

---

## 📖 Command Reference

### Information / Help
| Command | Description |
|---|---|
| `-h`, `--help` | Show the help message and exit. |

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

## 🤝 Credits

*   Original approach and C++ implementation by the [Electron team](https://github.com/electron/rcedit).
*   This Python port brings frictionless, scriptable metadata modification to typical continuous integration (CI) workflows on Windows using Python.
