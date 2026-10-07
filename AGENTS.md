# crate-cataloger

## Verify

Run before every push (PR CI runs the same command):

```sh
python3 -m py_compile crate.py
```

Tests: `python3 -m unittest discover -s tests`
