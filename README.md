# mcp-llmnetops

MCP (Model Context Protocol) server untuk operasi **read-only** pada perangkat jaringan.
LLM (Claude, Copilot, dll.) bisa melihat routing table, interface, BGP, log, config, dan ping
perangkat jaringan Anda — hanya melalui daftar perintah yang di-whitelist per platform.

## Platform yang didukung

| Platform key | Vendor / OS | Contoh perintah |
|---|---|---|
| `mikrotik-ros6` | MikroTik RouterOS 6 | `/ip route print`, `/routing bgp peer print`, `/export` |
| `mikrotik-ros7` | MikroTik RouterOS 7 | `/ip route print`, `/routing bgp session print`, `/export` |
| `cisco-ios` | Cisco IOS | `show ip route`, `show ip bgp summary` |
| `cisco-iosxe` | Cisco IOS XE | `show ip route`, `show ip bgp summary` |
| `cisco-iosxr` | Cisco IOS XR | `show route`, `show bgp summary` |
| `cisco-nxos` | Cisco NX-OS | `show ip route`, `show ip bgp summary` |
| `juniper-junos` | Juniper Junos | `show route`, `show bgp neighbor` |
| `aruba-aos-cx` | Aruba AOS-CX | `show ip route`, `show bgp summary` |
| `huawei-vrp` | Huawei VRP | `display ip routing-table`, `display bgp peer` |
| `ruckus-fastiron` | Ruckus FastIron | `show ip route`, `show ip bgp summary` |

Setiap platform hanya bisa menjalankan perintah yang terdaftar di
[`src/mcp_llmnetops/platforms.py`](src/mcp_llmnetops/platforms.py) — perintah lain ditolak.
Perintah `ping` membutuhkan parameter `target`.

## Instalasi

### Dengan pipx

```bash
pipx install mcp-llmnetops
```

### Dari source

```bash
git clone https://github.com/ratnoub/mcp-llmnetops.git
cd mcp-llmnetops
pipx install .
# atau untuk development:
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
```

## Konfigurasi device

Salin `devices.example.yaml` menjadi `devices.yaml` dan isi dengan device Anda:

```yaml
devices:
  - name: mikrotik-core-01
    host: 192.168.1.1
    platform: mikrotik-ros7
    username: admin
    password: ${MIKROTIK_CORE01_PASSWORD}   # dari environment variable

  - name: cisco-edge-01
    host: 10.0.0.1
    platform: cisco-ios
    username: netops
    password: ${CISCO_EDGE01_PASSWORD}
```

Field yang tersedia per device:

| Field | Wajib | Default | Keterangan |
|---|---|---|---|
| `name` | ya | — | Nama unik device (dipakai LLM) |
| `host` | ya | — | IP atau hostname |
| `platform` | ya | — | Salah satu platform key di tabel di atas |
| `username` | ya | — | Username SSH |
| `password` | salah satu | — | Password SSH (atau `ssh_key`) |
| `ssh_key` | salah satu | — | Path ke file private key SSH |
| `port` | tidak | `22` | Port SSH |
| `timeout` | tidak | `60` | Timeout per perintah (detik) |
| `known_hosts` | tidak | `auto` | `auto` (simpan key baru), `strict` (`~/.ssh/known_hosts`), `no-check` |

Nilai string bisa memakai environment variable: `${NAMA_VAR}`.

**Lokasi file config** (urutan pencarian):
1. `--config <path>`
2. Environment variable `MCP_LLMNETOPS_CONFIG`
3. `./devices.yaml` (working directory)
4. `~/.config/mcp-llmnetops/devices.yaml`

## Registrasi ke MCP client

### Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "llmnetops": {
      "command": "mcp-llmnetops",
      "args": ["--transport", "stdio", "--config", "C:/path/to/devices.yaml"],
      "env": {
        "MIKROTIK_CORE01_PASSWORD": "rahasia",
        "CISCO_EDGE01_PASSWORD": "rahasia"
      }
    }
  }
}
```

### Copilot CLI / client lain (stdio)

```json
{
  "mcpServers": {
    "llmnetops": {
      "command": "mcp-llmnetops",
      "args": ["--transport", "stdio"],
      "env": {
        "MCP_LLMNETOPS_CONFIG": "C:/path/to/devices.yaml"
      }
    }
  }
}
```

### Akses via HTTP (streamable-http)

Secara default server berjalan sebagai **HTTP server** di port **5758**,
bisa diakses di `http://<IP>:5758/mcp`. Cocok untuk client MCP yang mendukung
transport streamable-HTTP.

```bash
# jalankan server (default sudah streamable-http di 0.0.0.0:5758)
mcp-llmnetops --config devices.yaml
```

Client MCP cukup menunjuk ke endpoint:

```
http://<IP-server>:5758/mcp
```

> Catatan: karena default-nya HTTP, client berbasis **stdio** (Claude Desktop,
> Copilot CLI) harus menambahkan `--transport stdio` seperti contoh di atas.

## Tools yang tersedia

Setiap perintah whitelisted terdaftar sebagai **tool MCP tersendiri** (satu tool per
platform × perintah), sehingga LLM cukup memanggil tool yang tepat tanpa perlu
menyebutkan string perintah. Total **95 tool**: 3 tool umum + 92 tool per-perintah.

### Tool umum

| Tool | Fungsi |
|---|---|
| `list_devices` | Daftar device yang terkonfigurasi |
| `list_platforms` | Daftar platform yang didukung |
| `test_connection(device)` | Tes koneksi SSH ke device |

### Tool per-perintah

Nama tool mengikuti skema `<platform>_<perintah>`, dengan platform key memakai
underscore (mis. `cisco-ios` → `cisco_ios`, `aruba-aos-cx` → `aruba_aos_cx`).
Semua tool menerima parameter `device` (nama device dari `list_devices`).
Tool `ping` tambahan menerima parameter wajib `target`.

Contoh nama tool:

| Tool | Perintah yang dijalankan |
|---|---|
| `mikrotik_ros7_ip_route_print` | `/ip route print` |
| `mikrotik_ros7_routing_bgp_session_print` | `/routing bgp session print` |
| `cisco_ios_show_ip_route` | `show ip route` |
| `cisco_ios_show_ip_bgp_summary` | `show ip bgp summary` |
| `cisco_iosxr_show_route` | `show route` |
| `juniper_junos_show_bgp_neighbor` | `show bgp neighbor` |
| `huawei_vrp_display_ip_routing_table` | `display ip routing-table` |
| `aruba_aos_cx_show_bgp` | `show bgp` |
| `cisco_ios_ping` | `ping <target>` |

Contoh alur penggunaan oleh LLM:

```
list_devices()
  → "- mikrotik-core-01: 192.168.1.1:22 [mikrotik-ros7] user=admin"

mikrotik_ros7_ip_route_print(device="mikrotik-core-01")
  → "<routing table output>"

cisco_ios_ping(device="cisco-edge-01", target="8.8.8.8")
  → "<ping output>"
```

## Keamanan

- **Whitelist ketat**: hanya perintah yang terdaftar per platform yang bisa dijalankan.
  Tidak ada eksekusi perintah arbitrer.
- **Read-only**: semua perintah yang diizinkan bersifat read-only (show/print/display/ping).
- **Kredensial**: disarankan memakai environment variable (`${VAR}`) alih-alih plaintext.
- **SSH host key**: mode `auto` menyimpan host key baru di
  `~/.config/mcp-llmnetops/known_hosts`; gunakan `strict` untuk verifikasi ketat.
- File `devices.yaml` mengandung kredensial — jangan di-commit (sudah ada di `.gitignore`).

## Development

```bash
pip install -e ".[dev]"
pytest
```

Jalankan server manual:

```bash
mcp-llmnetops --config devices.yaml            # HTTP di http://0.0.0.0:5758/mcp (default)
mcp-llmnetops --transport stdio                # stdio (untuk client yang launch proses)
mcp-llmnetops --port 9999                      # ganti port HTTP
mcp-llmnetops --host 127.0.0.1                 # bind ke localhost saja
mcp-llmnetops --version
```

## Lisensi

MIT