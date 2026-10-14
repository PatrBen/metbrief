import os
from curl_cffi import requests
import shutil
from bs4 import BeautifulSoup
import datetime
try:
    from user_details_jan import *
except ImportError:  # fall back to the shipped template for everybody else
    from user_details import *
from urllib.parse import urlparse
import re
import argparse
from fake_useragent import UserAgent

# Optional .env / environment (takes precedence over user_details.py), see .env.example
if os.path.isfile('.env'):
    for _line in open('.env', encoding='utf-8'):
        _key, _sep, _val = _line.strip().partition('=')
        if _sep and not _key.startswith('#'):
            os.environ.setdefault(_key.strip(), _val.strip().strip('"\''))
USERNAME_TOPMETEO = os.environ.get('TOPMETEO_USER', USERNAME_TOPMETEO)
PASSWORD_TOPMETEO = os.environ.get('TOPMETEO_PASSWORD', PASSWORD_TOPMETEO)

LOC_COMP = 'jwgc2026'

detail_comp = {'wgc2026':
                    {'sounding_dict': {'12425': '961', '12575': '961',  '12374': '961'},
                     'locations_sat': ['mitteleuropa', 'polen'],
                     'loc_topmeteo': 'pl',
                     'locations_rad': ['polen']},
               'jwgc2026':
                    {'sounding_dict': {'10739': '961', '10771': '961',  '10548': '961'},
                     'locations_sat': ['mitteleuropa', 'deutschland'],
                     'loc_topmeteo': 'de',
                     'locations_rad': ['deutschland'],
                     'model_info':
                         {'model_list': ['sui-hd'],
                          'var_model_list':
                              ['bewoelkungsgrad', 'bedeckungsgrad-low-clouds', 'bedeckungsgrad-mid-clouds'],
                          'loc_model': 'deutschland', 'init_hour': '00',
                          'today_model': datetime.date.today().strftime('%Y%m%d'),
                          'hour_model_list': [str(i).zfill(2) for i in range(10, 16, 2)],
                          }},

                'prievidza_25':
                    {'sounding_dict': {'11952': '961', '12575': '961', '11747': '961', '12843': '961'},
                     'locations_sat': ['mitteleuropa', 'slowakei'],
                     'loc_topmeteo': 'cz',
                     'locations_rad': ['slowakei'],
                     'model_info':
                         {'model_list': ['deu-hd', 'euro', 'swisshd-nowcast'],
                          'var_model_list':
                              ['bewoelkungsgrad', 'bedeckungsgrad-low-clouds', 'bedeckungsgrad-mid-clouds'],
                          'loc_model': 'slowakei', 'init_hour': '00',
                          'today_model': datetime.date.today().strftime('%Y%m%d'),
                          'hour_model_list': [str(i).zfill(2) for i in range(8, 18, 2)],
                          }},


                'tabor_25': {'sounding_dict': {'10771': '942', '11520': '942', '11747': '942'},
                                            'locations_sat': ['mitteleuropa', 'tschechische-republik'],
                                            'loc_topmeteo': 'cz',
                                            'locations_rad': ['tschechische-republik']},
               }


def main():

    parser = argparse.ArgumentParser(
        description="Metbrief script"
    )
    parser.add_argument("--satrad_only", required=False, action="store_true",
                        help="Set to True to only download satellite and radar charts")
    parser.add_argument("--create_presentation_locally", required=False, action="store_true",
                        help="Set to True to create the presentation locally. PRESENTLY DISABLED")
    parser.add_argument("--output_path", required=False, type=str,
                        help="Set output path")
    parser.add_argument("--skysight", required=False, type=str, metavar="SPOT",
                        help="Also download Skysight layers + PPTX for SPOT from spots.example.yaml / spots.yaml "
                             "(needs skysight_login.py once)")
    args = parser.parse_args()

    satrad_only = args.satrad_only
    create_presentation_locally = False  # args.create_presentation_locally
    output_path = args.output_path

    ua = UserAgent()
    user_agent = {'User-agent': ua.random}

    pres_today_string = None
    if create_presentation_locally:
        # Copy template to daily directory and cleanup if needed
        today = datetime.date.today().strftime('%m%d')
        os.chdir(f'briefings/{LOC_COMP}')
        if not os.path.isdir(today):
            shutil.copytree(f'template_{LOC_COMP}', today)
            shutil.rmtree(f'{today}/charts')
            os.mkdir(f'{today}/charts')

        # Rename presentation
        os.chdir(f'{today}')
        pres_template_string = f'template_{LOC_COMP}.odp'
        pres_today_string = pres_template_string.replace('template', today)
        if os.path.exists(pres_template_string):
            os.rename(pres_template_string, pres_today_string)
        os.chdir('charts')
    else:
        if output_path:
            base_path = os.path.join(output_path, 'meteo', 'charts')
        else:
            base_path = os.path.join('briefings', f'{LOC_COMP}', 'charts')

        os.makedirs(base_path, exist_ok=True)
        os.chdir(base_path)

    s = requests.Session(impersonate="chrome146")
    s.get("https://kachelmannwetter.com")

    keys_charts = detail_comp[LOC_COMP].keys()

    if satrad_only:
        # Download kachelmannwetter.com satellite images
        if 'locations_sat' in keys_charts:
            for nl, loc in enumerate(detail_comp[LOC_COMP]['locations_sat']):
                url = f'https://kachelmannwetter.com/de/sat/{loc}/satellit-satellit-hd-10m-superhd.html'
                download_kachelmann(s, url, type_data='sat', loc_in=loc, n_loc=nl)

        # Download kachelmannwetter.com radar images
        if 'locations_rad' in keys_charts:
            for nl, loc in enumerate(detail_comp[LOC_COMP]['locations_rad']):
                url = f'https://kachelmannwetter.com/de/regenradar/{loc}'
                download_kachelmann(s, url, type_data='radar', loc_in=loc, n_loc=nl)
    else:
        # Download kachelmannwetter.com weather charts
        if 'model_info' in keys_charts:
            model_info = detail_comp[LOC_COMP]['model_info']
            for model_use in model_info['model_list']:
                for var_model in model_info['var_model_list']:
                    for hour_model in model_info['hour_model_list']:
                        url = (f'https://kachelmannwetter.com/de/modellkarten/{model_use}/'
                               f'{model_info["today_model"]}{model_info["init_hour"]}/{model_info["loc_model"]}/'
                               f'{var_model}/{model_info["today_model"]}-{hour_model}00z.html')
                        download_kachelmann(s, url, 'model', loc_in=None, model_var=var_model)

        # Download kachelmannwetter.com soundings images
        if 'sounding_dict' in keys_charts:
            today_sounding = datetime.date.today().strftime('%Y%m%d')
            nl = 0
            for station, area_id_sounding in detail_comp[LOC_COMP]['sounding_dict'].items():
                url = (f'https://kachelmannwetter.com/de/ajax/obsdetail?station_id=R{station}'
                       f'&timestamp={today_sounding}0000&param_id=1&model=obsradio'
                       f'&area_id={area_id_sounding}&counter=true&lang=DE')
                download_kachelmann(s, url, 'sounding', n_loc=nl)
                nl += 1
                
        # Download DWD and wetter3 charts
        if not os.path.isdir('gwl'):
            os.mkdir('gwl')
        else:
            shutil.rmtree('gwl')
            os.mkdir('gwl')

        for chart in ['bwk_bodendruck_na_ana', 'ico_500ht_na_ana']:
            file_url = f'https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten/{chart}.png'
            request_download(file_url, user_agent, opath='gwl/')
        request_download('https://wetter3.de/Animation_00_UTC/12_10.gif', user_agent, opath='gwl/')

            
        # Set variables that should be downloaded from topmeteo
        var_topmeteo = {'pfd': 28, 'thermik': 24, 'wolken': 26, 'wind_1500': 39}
        # Set date
        today = datetime.datetime.now()
        today = today.replace(hour=0, minute=0, second=0, microsecond=0)
        # Topmeteo chart download
        download_topmeteo(var_topmeteo, loc=detail_comp[LOC_COMP]['loc_topmeteo'],
                          day=0, today=today, user=USERNAME_TOPMETEO, passwd=PASSWORD_TOPMETEO)

    if args.skysight:
        # Optional: Skysight layers into charts/skysight (cwd is the charts directory here)
        import skysight_shots
        key, spot = skysight_shots.find_spot(args.skysight)
        if spot is None:
            print(f"Skysight: unknown spot '{args.skysight}' (see spots.example.yaml)")
        else:
            skysight_shots.download(spot, datetime.date.today(), os.getcwd(), headless=True, key=key)

    if create_presentation_locally:
        # Verify if command-line LibreOffice is available
        os.chdir('..')
        if shutil.which('soffice'):
            # Convert presentation to PDF
            os.system(f'soffice --headless --convert-to pdf {pres_today_string}')


def download_topmeteo(var_dict, loc=None, day=0, today=None, user=None, passwd=None):

    # Create topmeteo directory in carts
    if not os.path.isdir('topmeteo'):
        os.mkdir('topmeteo')
    else:
        shutil.rmtree('topmeteo')
        os.mkdir('topmeteo')

        
    # 1. Start a persistent session and set referer
    session = requests.Session(impersonate='chrome146')
    session.headers.update({
        'Referer': 'https://vfr.topmeteo.eu/de/de/login/'
    })

    # 2. Get the initial cookies and HTML to find the CSRF token
    login_url = "https://vfr.topmeteo.eu/de/de/login/?next="
    response = session.get(login_url)
    soup = BeautifulSoup(response.text, 'html.parser')
    csrf_token = soup.find('input', {'name': 'csrfmiddlewaretoken'})['value']

    # 3. Build the payload
    # Use the same keys identified in the browser's "Payload" tab
    payload = {
        'csrfmiddlewaretoken': csrf_token,
        'username': user,
        'password': passwd,
        'next': ''
    }

    # 4. Perform the POST and check for correct login
    post_response = session.post(login_url, data=payload)
    soup = BeautifulSoup(post_response.text, 'html.parser')
    logout_element = soup.find(string=re.compile("Ausloggen", re.IGNORECASE))

    # 5. Verify and Download
    if post_response.status_code == 200 and logout_element:
        print("TopMeteo login successful!")

        for key, var in var_dict.items():
            if key == 'pfd':
                time_steps = [0]
            else:
                time_steps = range(8, 17)

            var_path = f'topmeteo/{key}'
            if not os.path.isdir(var_path):
                os.mkdir(var_path)

            for time_data in time_steps:
                filename = f'{key}_{day}_{time_data}.png'
                time_step = today.replace(hour=12).strftime("%Y-%m-%dT%H:%M:%SZ")
                download_url = f'https://vfr.topmeteo.eu/de/{loc}/map/{var}/{day}/{time_data}/image?{time_step}'
                session_download_url = requests.get(download_url, cookies=post_response.cookies)
                open(f'{var_path}/{filename}', 'wb').write(session_download_url.content)
    else:
        print("TopMeteo login unsuccessful!")


def download_kachelmann(session, url_in, type_data=None, loc_in=None, model=None, model_var=None, n_loc=0):
    """
    Downloads the latest satellite, radar, model, or sounding images from Kachelmannwetter.

    Parameters:
    - session (requests.Session): Active session with shared cookies and headers.
    - url_in (str): The URL of the webpage containing the image.
    - user_agent (dict): Headers to use in HTTP requests, e.g., {'User-Agent': '...'}.
    - type_data (str): Type of data ('sat', 'radar', 'model', 'sounding').
    - loc_in (str): Location identifier
    - model (str, optional): Model name for structured output (used with 'model' type).
    - model_var (str, optional): Model variable for structured output (used with 'model' type).

    Returns:
    - str: The full path of the downloaded image file, or None if download failed.
    """

    # Ensure output directory exists
    if model:
        opath = os.path.join(type_data, model, model_var)
    else:
        opath = type_data

    if not os.path.isdir(opath):
        os.makedirs(opath, exist_ok=True)

    try:
        # Fetch webpage content using the provided session
        response = session.get(url_in)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        # Parse HTML to find the image URL
        download_url, filename = None, None

        if type_data in ['sat', 'radar', 'model']:
            meta_tag = soup.find('meta', property='og:image')
            if not meta_tag or 'content' not in meta_tag.attrs:
                print("Error: Image URL not found in meta tag.")
                return None
            download_url = meta_tag['content']
            if type_data == 'model':
                filename_split = os.path.basename(download_url).split('_')
                filename_split.pop(3)
                filename = os.path.join(opath, '_'.join(filename_split))
            else:
                filename = os.path.join(opath, os.path.basename(download_url))

        elif type_data == 'sounding':
            for img in soup.find_all('img'):
                for attr in ['data-src']:
                    url = img.get(attr)
                    if url and url.lower().endswith('.png'):
                        download_url = url
                    else:
                        print("Error: Image URL not found")
                        return None

            # Split on underscores and remove the last part before .png and rejoin all except the last part before .png
            filename_split = os.path.basename(download_url).split('_')
            filename = os.path.join(opath, '_'.join(filename_split[:-2]) + '.png')

        # Download image if it doesn't exist
        if os.path.isfile(filename):
            print(filename)
            os.remove(filename)

        # Download image if it doesn't exist
        if not os.path.isfile(filename):
            img_response = session.get(download_url)
            img_response.raise_for_status()

            with open(filename, 'wb') as file:
                file.write(img_response.content)

        # Create a symbolic link to the latest image
        link_filename = os.path.join(type_data, f"{type_data}_{loc_in}_latest.png")

        if os.path.isfile(link_filename):
            os.remove(link_filename)

        if type_data in ['sat', 'radar']:
            shutil.copyfile(filename, link_filename)

        return filename

    except requests.exceptions.RequestException as e:
        print(f"Error during download: {e}")
        return None


def request_download(url_in, user_agent, opath=''):
    """
    Downloads a file using Python's requests module with optional authentication.

    Parameters:
    - url_in (str): The URL of the file to be downloaded.
    - opath (str, optional): Output directory where the file will be saved (default: current directory).
    - user (str, optional): Username for authentication (default: None).
    - passwd (str, optional): Password for authentication (default: None).

    Returns:
    - str: The full path of the downloaded file if successful, None otherwise.

    Notes:
    - If the filename cannot be determined from the URL, it defaults to "downloaded_file".
    - Uses "Mozilla/5.0" as the user-agent to mimic a web browser.
    - Ensures proper file handling using a context manager.
    """
    # Extract filename safely
    parsed_url = urlparse(url_in)

    if 'flugwetter' not in url_in:
        filename = os.path.basename(parsed_url.path) or "downloaded_file"
    else:
        filename = url_in.split('=')[-1]

    # Ensure output directory exists
    if opath and not os.path.exists(opath):
        os.makedirs(opath)

    file_path = os.path.join(opath, filename)

    # Start session and download file
    session = requests.Session()
    try:
        response = session.get(url_in, headers=user_agent, stream=True)
        response.raise_for_status()  # Raise an error for HTTP issues

        # Write content to file
        with open(file_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        return file_path

    except requests.exceptions.RequestException as e:
        print(f"Error during download: {e}")
        return None


if __name__ == "__main__":
    main()
