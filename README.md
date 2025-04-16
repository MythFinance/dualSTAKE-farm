# Puya optimizer bug repro

Managed to break the puya optimizer with an empty assertion error

Could be related to the `get_tinyman_algo_price_for_asset` subroutine, as this starts building with the default optimizer if I swap its body with `return 0`

puya builds this with optimization-level 0 (log-opt0.txt with debug, log-opt0-nodebug.txt without debug)

with default optimizations it results in `opt.txt`

My algokit doctor output:

```
timestamp: 2025-04-16T21:21:53+00:00
AlgoKit: 2.6.2
AlgoKit Python: 3.12.4 (main, Jun  8 2024, 18:29:57) [GCC 11.4.0] (location: /home/bit/.local/pipx/venvs/algokit)
OS: Linux-6.0.8-x86_64-with-glibc2.35
docker: 27.0.3
docker compose: 2.28.1
git: 2.34.1
python: 2.7.18 (location: /usr/local/bin/python)
python3: 3.10.12 (location: /usr/bin/python3)
pipx: 1.7.1
poetry: 1.8.3
node: 20.11.0
npm: 10.2.4
```
