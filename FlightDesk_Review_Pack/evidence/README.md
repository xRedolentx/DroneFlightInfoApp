# Review evidence

Run only in a disposable checkout: `python evidence/review_probes.py /path/to/DroneFlightInfoApp`.

The 19 probes print baseline behaviors; they are not passing regression tests. Some parser outcomes are deliberately mocked to isolate archive/identity behavior. They create temporary synthetic roots and do not access owner logs or call external services. After repairs, outputs should change; convert each relevant scenario into an assertion-based regression test.

Reviewed commit: 7806e1f554a3959672c4bd15dd4b6e52638d0fc0. See FLIGHTFIX.md for findings, full-suite limitations and instructions.
