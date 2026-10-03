# Farm TokenHarbor — di VPS baru

## Pasang (3 baris)

```bash
git clone https://x-access-token:TOKEN@github.com/Pakdhe14ww/th-farm.git
cd th-farm
sudo bash install/install_th_farm.sh
```

Itu saja. Paket, pool proxy (39.187), dan key solver sudah ada di dalam repo —
skrip memakai file dari hasil clone, tidak mengunduh apa pun.

Jalankan tanpa `sudo`? Skrip akan bilang butuh root. Setelan opsional:

```bash
sudo TARGET=5000 WORKERS=20 bash install/install_th_farm.sh
```

| env | arti | default |
|---|---|---|
| `TARGET` | jumlah key yang diburu | 5000 |
| `WORKERS` | worker paralel | 20 |
| `DIR` | tempat pasang | /opt/thfarm |
| `SERVICE` | nama layanan systemd | thfarm |

## Setelah terpasang

```bash
systemctl status thfarm      # status
tail -f /opt/thfarm/farm.log # log
wc -l /opt/thfarm/akun.txt   # jumlah key
systemctl stop thfarm        # berhenti
```

## Kalau repo public

```bash
git clone https://github.com/Pakdhe14ww/th-farm.git && cd th-farm
sudo bash install/install_th_farm.sh
```

## Isi repo

| berkas | isi |
|---|---|
| `pkg/` | engine farm (3 modul python) |
| `proxies_all.txt` | 39.187 proxy |
| `peak_keys.txt` | 145 key solver peak.fo |
| `install/install_th_farm.sh` | installer |
| `.github/workflows/farm.yml` | farm di GitHub Actions (20 shard) |
