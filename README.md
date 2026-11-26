# OptionsLens v1.0

Analisi delle opzioni di un titolo e scanner su una lista. Dati CBOE ritardati (cdn.cboe.com), solo libreria standard Python.
Render: Web Service, Build Command `echo ok`, Start Command `python server.py`. Facoltativa: variabile APP_PASSWORD.
Prova offline: `DEMO_DATA=1 python server.py`. Test: `python test_opt.py`.
Strumento educativo, non consulenza finanziaria.
Stress test: scheda "Stress" per simulare un crollo e il ribilanciamento con coperture (bear put spread, put ratio backspread, delta hedge). Calcolo puro in `opt.simulate_crash_rebalance`, endpoint `/api/stress`.
