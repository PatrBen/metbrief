
## Installation (tested on macOS and Windows)

1. Install LibreOffice for your [respective system](https://de.libreoffice.org/download/download/)

2. Install Miniconda or Anaconda (if not already done). Follow the [respective instructions](https://docs.conda.io/projects/conda/en/latest/user-guide/install/index.html#regular-installation) for your operating system.

3. Download a zipped version of the repository (Click on Code and you will find a button to download it) and unzip it. 

4. Alternatively, it also can be downloaded via git.
    ```bash
   git clone https://github.com/jkretz/metbrief.git
    ```
5. Open a Terminal / Anaconda Prompt (under Windows) and navigate to the (unziped) directory of the repository.

6. Create the conda environment and install the needed anaconda packages:
   ```bash
   conda env create -f environment.yml
   conda activate metbrief
   ```
   
7. Enter your TopMeteo user detail in `user_details.py`.
   
## Run

From the `metbrief` directory, run:

   ```bash
   python metbrief.py
   ```

This should create a new directory of today's date in the `briefing` directory for the respective location.
   

## Skysight integration (optional)

Adds Skysight map layers (potential flight distance, Cu base, ...) and the wind diagram for one spot, plus a simple PPTX with one slide per image. Without `--skysight` nothing changes in `metbrief.py`.

Requirements:

- Your own [Skysight](https://skysight.io) account (and, as before, your own TopMeteo account).
- Playwright and the Python packages from `requirements.txt` / `environment.yml` (`pillow`, `python-pptx`, `pyyaml`, `playwright`), then once:
   ```bash
   playwright install chromium
   ```

Setup:

1. Credentials: copy `.env.example` to `.env` and fill in your TopMeteo login. Values in `.env` (or the environment) take precedence over `user_details.py`; without `.env` everything works as before. `.env` is git-ignored. No Skysight password is read or stored by the scripts.
2. Spots: `spots.example.yaml` contains Feuerstein as an example (coordinates, time zone, Skysight region, layers). Copy it to `spots.yaml` to add your own spots (git-ignored, looked up first).
3. First run, once: `python skysight_login.py`. A visible browser opens, log in to Skysight by hand. The session is stored locally in `skysight_profile/` (git-ignored). Repeat if the session expires.

Run:

```bash
python metbrief.py --skysight feuerstein        # together with the usual download
python skysight_shots.py --spot feuerstein      # Skysight only; options --date YYYY-MM-DD, --headless, --no-pptx, --out DIR
```

Output (next to the other charts, i.e. `briefings/<location>/charts/skysight/`, or `<output_path>/meteo/charts/skysight/` with `--output_path`): `<layer>_<HH>.png`, `windprofil.png`, `skysight_windgram.json` (hourly boundary layer / condensation level / climb), `status_skysight.json` (what worked) and `skysight_<spot>_<date>.pptx`. Without a profile or login the script stops with a clear message and exit code 1.

Terms of use: use only your own accounts and only for private purposes. Check the terms of use of Skysight and TopMeteo; the maps are their content and must not be redistributed.
