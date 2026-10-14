"""Download Skysight map layers (PFD, cloud base, ...) and the wind diagram for one spot.

    python skysight_shots.py --spot feuerstein [--date YYYY-MM-DD] [--out DIR] [--headless] [--no-pptx]

Needs the browser profile created by skysight_login.py (skysight_profile/).
Output: <out>/skysight/<layer>_<HH>.png, windprofil.png, skysight_windgram.json, status_skysight.json
and (unless --no-pptx) <out>/skysight/skysight_<spot>_<date>.pptx.
Exit code: 0 = all images ok, 1 = setup problem (no spot/profile/playwright), 2 = some images failed.
No credentials are read or stored here: the login is done once by hand in skysight_login.py.
"""
import argparse
import datetime as dt
import io
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / 'skysight_profile'
SPOT_FILES = [ROOT / 'spots.yaml', ROOT / 'spots.example.yaml']   # own file wins over the example
APP_URL = 'https://skysight.io/secure/'
LOGIN_HINT = 'Skysight login missing or expired: run python skysight_login.py'

VIEWPORT = {'width': 1920, 'height': 1080}        # maps are always taken in the desktop layout
SCALE = 1.5                                       # screenshot pixel density
SIDEBAR_WIDTH = 224                               # left sidebar, cropped from the map images
MAP_ASPECT = 1666 / 1027                          # map area width/height at VIEWPORT
WINDGRAM_VIEWPORT = {'width': 790, 'height': 800}  # mobile layout: whole day fits into the diagram
DEFAULT_WIDTH_KM = 800
RETRIES = 2                                       # additional attempts per image
MAP_IDLE_TIMEOUT_MS = 15000
DEBOUNCE_MS = 300
MIN_BYTES = 40_000
MIN_STDDEV = 12                                   # grey-level spread; below this the image counts as empty

# Selectors / page internals. Skysight changes its page now and then: adjust here.
SEL_MAP = 'canvas.maplibregl-canvas'
SEL_SLIDER = 'input.input-range'
SEL_UNIT = 'button.unit-changer'
SEL_SETTINGS = 'button.settings-button'
SEL_REGION = 'li.region'
SEL_MODEL = 'span.skysight-model'
SEL_DAY = '.day-select'
SEL_UPDATED = '.updated'
SEL_LEGEND = '.legend'
WINDGRAM_DATA_URL = '/v2/windgram/'
REGION_CODE_JS = "(JSON.parse(localStorage.getItem('skysight_profile_local_settings') || '{}').region || null)"
# Without these Chrome throttles a covered/minimised window: the map stops rendering.
BROWSER_ARGS = ['--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
                '--disable-background-timer-throttling', '--disable-features=CalculateNativeWinOcclusion']


class ShotError(Exception):
    pass


# --------------------------------------------------------------------------- configuration

def find_spot(query):
    """Look query up (case-insensitive) in key, name and aliases of the spot files. -> (key, spot) or (None, None)."""
    wanted = str(query).strip().casefold()
    for path in SPOT_FILES:
        if not path.is_file():
            continue
        spots = (yaml.safe_load(path.read_text(encoding='utf-8')) or {}).get('spots') or {}
        for key, spot in spots.items():
            if wanted in (str(n).casefold() for n in [key, spot.get('name', ''), *(spot.get('aliases') or [])]):
                return key, spot
    return None, None


def view_from_bbox(bbox):
    """bbox [south, west, north, east] -> (center [lat, lon], map width km) so that the box fits the map area."""
    south, west, north, east = bbox
    lat, lon = (south + north) / 2, (west + east) / 2
    width_km = (east - west) * 111.32 * math.cos(math.radians(lat))
    height_km = (north - south) * 110.57
    return [round(lat, 4), round(lon, 4)], round(max(width_km, height_km * MAP_ASPECT))


def skysight_config(spot):
    cfg = dict(spot.get('skysight') or {})
    view = dict(cfg.get('view') or {})
    if view.get('bbox') and not view.get('map_width_km'):
        view['center'], view['map_width_km'] = view_from_bbox(view['bbox'])
    view.setdefault('center', [spot['lat'], spot['lon']])
    view.setdefault('map_width_km', DEFAULT_WIDTH_KM)
    cfg['view'] = view
    cfg.setdefault('point', [spot['lat'], spot['lon']])
    cfg.setdefault('layers', [])
    return cfg


def targets(spot):
    """Expected images: dicts with file, kind ('map'|'windgram'), layer, hour, title, note."""
    cfg = skysight_config(spot)
    result = []
    for layer in cfg['layers']:
        for hour in layer.get('times_local') or [14]:
            title = layer['title'] if layer.get('daily') else f"{layer['title']} {hour} lcl"
            result.append({'file': f"skysight/{layer['key']}_{hour:02d}.png", 'kind': 'map', 'layer': layer,
                           'hour': hour, 'title': title, 'note': layer.get('note')})
    if cfg.get('windgram'):
        result.append({'file': 'skysight/windprofil.png', 'kind': 'windgram', 'layer': None, 'hour': None,
                       'title': cfg['windgram'].get('title', 'Skysight wind profile'), 'note': None})
    return result


# --------------------------------------------------------------------------- waiting and checking

def wait_map(page):
    """Wait until the map finished rendering (MapLibre 'idle'). False if the time limit was hit."""
    page.wait_for_timeout(DEBOUNCE_MS)
    return page.evaluate("""(ms) => new Promise(resolve => {
        const timer = setTimeout(() => resolve(false), ms);
        skysightMap.once('idle', () => { clearTimeout(timer); resolve(true); });
        skysightMap.triggerRepaint();
    })""", MAP_IDLE_TIMEOUT_MS)


def wait_app(page):
    page.wait_for_function('() => window.skysightMap && skysightMap.loaded()', timeout=45000)
    page.wait_for_selector(SEL_LEGEND, timeout=15000)
    page.wait_for_selector(SEL_DAY, timeout=15000)


def dismiss_overlays(page, wait_ms=0):
    """Close the 'tip of the day' dialog (.tip-modal, button 'Ok!') that covers the whole page.
    Matches by class, so it works in any UI language. wait_ms > 0 waits that long for it to appear
    (page start); 0 only checks the current state. Never raises: no dialog = nothing to do."""
    modal = page.locator('.tip-modal')
    try:
        if wait_ms:
            modal.first.wait_for(state='visible', timeout=wait_ms)
        if not modal.first.is_visible():
            return
        try:
            page.locator('.tip-modal__button').first.click(timeout=3000)
        except Exception:
            page.keyboard.press('Escape')
        modal.first.wait_for(state='hidden', timeout=3000)
    except Exception:
        pass


def assert_logged_in(page):
    if 'login' in urlsplit(page.url).path:
        raise ShotError(LOGIN_HINT)


def check_image(data):
    if len(data) < MIN_BYTES:
        raise ShotError(f'image too small ({len(data)} bytes)')
    spread = ImageStat.Stat(Image.open(io.BytesIO(data)).convert('L')).stddev[0]
    if spread < MIN_STDDEV:
        raise ShotError(f'image practically empty (pixel spread {spread:.1f})')


# --------------------------------------------------------------------------- driving the page

def ensure_region(page, region):
    """Switch the Skysight region (Settings > Regions) if another one is active."""
    if not region:
        return
    dismiss_overlays(page)
    page.locator(SEL_SETTINGS).click()
    page.wait_for_selector(SEL_REGION, timeout=10000)
    active = page.locator(SEL_REGION + '.active')
    if active.count() and active.first.inner_text().strip() == region:
        page.locator(SEL_SETTINGS).click()  # close menu
        return
    wanted = page.locator(SEL_REGION, has_text=region)
    if wanted.count() == 0:
        raise ShotError(f"Skysight region '{region}' does not exist")
    before = page.evaluate(REGION_CODE_JS)
    wanted.first.click()
    # The menu closes on click, so check the stored region code to know the switch was saved.
    page.wait_for_function(f'(old) => ({REGION_CODE_JS}) !== old', arg=before, timeout=15000)
    wait_map(page)
    page.reload(timeout=60000)
    dismiss_overlays(page, wait_ms=8000)
    wait_app(page)
    assert_logged_in(page)


def ensure_model(page):
    """Select the SkySight model explicitly (not ICON)."""
    model = page.locator(SEL_MODEL)
    if model.count() == 0:  # regions with a single model have no switch
        return
    if 'active' not in (model.get_attribute('class') or ''):
        model.click()
        wait_map(page)
        if 'active' not in (model.get_attribute('class') or ''):
            raise ShotError('cannot select the SkySight model')


def slider_day(page, tz):
    start = page.evaluate(f"Number(document.querySelector('{SEL_SLIDER}').min)")
    return dt.datetime.fromtimestamp(start, tz).date()


def select_day(page, day, tz):
    """Step to the wanted day with the arrows next to the date."""
    for _ in range(12):
        current = slider_day(page, tz)
        if current == day:
            return
        old = page.evaluate(f"Number(document.querySelector('{SEL_SLIDER}').min)")
        # .day-select exists twice (desktop/mobile): take the visible one; arrows are its child buttons.
        page.locator(f'{SEL_DAY}:visible').first.locator('button').nth(1 if day > current else 0).click()
        try:
            page.wait_for_function(f"(old) => Number(document.querySelector('{SEL_SLIDER}').min) !== old",
                                   arg=old, timeout=8000)
        except Exception:
            break
    raise ShotError(f'date {day} not available at Skysight (offered e.g. {slider_day(page, tz)})')


def set_time(page, day, hour, tz):
    stamp = int(dt.datetime(day.year, day.month, day.day, hour, tzinfo=tz).timestamp())
    ok = page.evaluate("""([sel, t]) => {
        const el = document.querySelector(sel);
        if (t < Number(el.min) || t > Number(el.max)) return false;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(t));
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return Number(el.value) === t;
    }""", [SEL_SLIDER, stamp])
    if not ok:
        raise ShotError(f'{hour:02d}:00 is outside the Skysight timeline')
    return stamp


def ensure_unit(page, unit):
    button = page.locator(SEL_UNIT)
    for _ in range(4):
        if button.inner_text().strip() == unit:
            return
        button.click()
        page.wait_for_timeout(DEBOUNCE_MS)
    raise ShotError(f"cannot set unit '{unit}' (shown: {button.inner_text().strip()})")


def set_view(page, view):
    """Set map centre and width through the page's map object. Returns the resulting width in km."""
    lat, lon = view['center']
    half = view['map_width_km'] / 2 / (111.32 * math.cos(math.radians(lat)))
    return page.evaluate("""([lat, lon, half]) => {
        skysightMap.fitBounds([[lon - half, lat], [lon + half, lat]], {animate: false, padding: 0});
        const b = skysightMap.getBounds();
        return Math.round((b.getEast() - b.getWest()) * 111.32 * Math.cos(lat * Math.PI / 180));
    }""", [lat, lon, half])


def shoot_map(page, target, cfg, day, tz):
    layer = target['layer']
    dismiss_overlays(page)
    page.set_viewport_size(VIEWPORT)
    page.evaluate('(route) => { location.hash = "#/" + route; }', layer['route'])
    page.wait_for_function('(route) => location.hash === "#/" + route', arg=layer['route'], timeout=5000)
    width_km = set_view(page, cfg['view'])
    stamp = set_time(page, day, target['hour'], tz)
    if layer.get('unit'):
        ensure_unit(page, layer['unit'])
    idle = wait_map(page)
    # Check what the page really shows instead of trusting a title in the image.
    shown = page.evaluate(f"Number(document.querySelector('{SEL_SLIDER}').value)")
    if shown != stamp or not page.url.endswith('#/' + layer['route']) or slider_day(page, tz) != day:
        raise ShotError('page does not show the requested layer/time')
    updated = page.locator(SEL_UPDATED).inner_text().strip() if page.locator(SEL_UPDATED).count() else None
    extra = {'layer_route': layer['route'], 'shown_local': f"{day.isoformat()} {target['hour']:02d}:00",
             'unit': layer.get('unit'), 'updated': updated, 'map_width_km': width_km}
    if not idle:
        extra['wait_timeout'] = True
    clip = {'x': SIDEBAR_WIDTH, 'y': 0, 'width': VIEWPORT['width'] - SIDEBAR_WIDTH, 'height': VIEWPORT['height']}
    return page.screenshot(clip=clip), extra


def windgram_numbers(payload, day, tz):
    """Hourly values of the briefing day from the data the page loads for the wind diagram.
    Keys = local hour; heights in m MSL (pblh = boundary layer, zsfclcl = condensation level)."""
    hours = {}
    for step in payload.get('data') or []:
        when = dt.datetime.strptime(step['dt'], '%Y%m%d%H%M').replace(tzinfo=dt.timezone.utc).astimezone(tz)
        if when.date() != day or when.minute != 0:
            continue
        climb = [float(level['ti']) for level in step.get('data') or [] if level.get('ti') not in (None, '')]
        hours[f'{when.hour:02d}'] = {
            'boundary_layer_msl': float(step['pblh']), 'condensation_level_msl': float(step['zsfclcl']),
            'cu_oktas': float(step.get('cu_oktas') or 0), 'max_climb_ms': round(max(climb), 1) if climb else None}
    return {'terrain_msl': float(payload.get('base') or 0), 'hours': hours}


def shoot_windgram(page, cfg, day, tz):
    dismiss_overlays(page)
    page.set_viewport_size(WINDGRAM_VIEWPORT)
    captured = {}

    def on_response(response):  # the page loads the numbers itself; we only listen
        if WINDGRAM_DATA_URL in response.url and response.status == 200:
            try:
                captured['payload'] = response.json()
            except Exception:
                pass
    page.on('response', on_response)
    route = cfg['layers'][0]['route'] if cfg['layers'] else 'pfd'
    page.evaluate('(route) => { location.hash = "#/" + route + "/windgram"; }', route)
    page.wait_for_function('() => location.hash.endsWith("/windgram")', timeout=5000)
    lat, lon = cfg['point']
    # Only move if the point is off-screen (a zoom change cost ~40 s).
    page.evaluate('([lat, lon]) => { if (!skysightMap.getBounds().contains([lon, lat])) '
                  'skysightMap.jumpTo({center: [lon, lat]}); }', [lat, lon])
    # Do not wait for 'idle' here (took ~40 s in this view); a standing map is enough for the click.
    page.wait_for_function('() => !skysightMap.isMoving()', timeout=5000)
    # Click the spot: screen position from the map projection, no fixed pixels.
    box = page.locator(SEL_MAP).bounding_box()
    x, y = page.evaluate('([lat, lon]) => { const p = skysightMap.project([lon, lat]); return [p.x, p.y]; }', [lat, lon])
    page.mouse.click(box['x'] + x, box['y'] + y)
    page.wait_for_selector('svg.mainSVG', timeout=20000)
    enabled = "() => { const s = document.querySelector('select.input-select'); return s && !s.disabled; }"
    select = page.locator('select.input-select')
    wanted = cfg['windgram']
    current = select.evaluate('el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ""')
    drawn = "() => document.querySelectorAll('svg.mainSVG path').length > 20"
    if wanted.get('range') and current.strip() != wanted['range']:
        page.wait_for_function(enabled, timeout=20000)  # the menu is locked from time to time
        select.select_option(label=wanted['range'], timeout=15000)
        page.wait_for_function(drawn, timeout=20000)
    if wanted.get('tab'):
        tab = page.locator('.tab', has_text=wanted['tab']).first
        if 'active' not in (tab.get_attribute('class') or ''):
            tab.click()
    page.wait_for_function(drawn, timeout=20000)
    page.wait_for_timeout(DEBOUNCE_MS)
    if slider_day(page, tz) != day:
        raise ShotError('wind diagram does not show the briefing day')
    image = page.screenshot()
    extra = {'range': wanted.get('range'), 'tab': wanted.get('tab')}
    try:
        for _ in range(20):  # up to 4 s for the data (normally there before the diagram)
            if 'payload' in captured:
                break
            page.wait_for_timeout(200)
        extra['numbers'] = windgram_numbers(captured['payload'], day, tz)
    except Exception as exc:
        extra['numbers_error'] = f'wind diagram numbers not readable ({type(exc).__name__})'
    page.remove_listener('response', on_response)
    page.locator('button.close-button').click()
    page.wait_for_selector('svg.mainSVG', state='detached', timeout=5000)
    return image, extra


# --------------------------------------------------------------------------- run

def _reason(exc):
    return str(exc) if isinstance(exc, ShotError) else f'{type(exc).__name__}: {str(exc)[:200]}'


def run(spot, day, todo, save, headless):
    """Take the images in todo; save(target, bytes) stores one. Returns a list of result entries."""
    from playwright.sync_api import sync_playwright
    cfg = skysight_config(spot)
    tz = ZoneInfo(spot['timezone'])
    entries = []

    def fail_rest(reason):
        done = {e['file'] for e in entries}
        entries.extend({'file': t['file'], 'ok': False, 'reason': reason} for t in todo if t['file'] not in done)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=headless, viewport=VIEWPORT, device_scale_factor=SCALE,
            timezone_id=spot['timezone'], locale='de-DE', args=BROWSER_ARGS)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(20000)
            page.goto(APP_URL, timeout=60000)
            assert_logged_in(page)
            dismiss_overlays(page, wait_ms=8000)
            try:
                wait_app(page)
                dismiss_overlays(page)
            except Exception:
                assert_logged_in(page)
                raise ShotError('Skysight map does not load (check login: run skysight_login.py)')
            ensure_region(page, cfg.get('region'))
            ensure_model(page)
            select_day(page, day, tz)
            for target in todo:
                error = None
                for attempt in range(1 + RETRIES):
                    try:
                        if target['kind'] == 'map':
                            image, extra = shoot_map(page, target, cfg, day, tz)
                        else:
                            image, extra = shoot_windgram(page, cfg, day, tz)
                        assert_logged_in(page)
                        check_image(image)
                        save(target, image)
                        entries.append({'file': target['file'], 'ok': True, 'reason': None,
                                        'attempts': attempt + 1, **extra})
                        error = None
                        break
                    except Exception as exc:
                        error = _reason(exc)
                        if error == LOGIN_HINT:
                            break
                if error:
                    entries.append({'file': target['file'], 'ok': False, 'reason': error})
                    if error == LOGIN_HINT:
                        break
        except Exception as exc:
            fail_rest(_reason(exc))
        finally:
            fail_rest('not run (earlier error)')
            try:
                context.close()
            except Exception:
                pass
    return entries


def download(spot, day, out_dir, headless=True, pptx=True, key=None):
    """Fetch all Skysight images of a spot into out_dir/skysight/. Returns the exit code (0/1/2)."""
    todo = targets(spot)
    if not todo:
        print('Spot has no skysight layers configured', file=sys.stderr)
        return 1
    if not PROFILE.is_dir():
        print(f'{LOGIN_HINT} (no profile in {PROFILE.name}/)', file=sys.stderr)
        return 1
    try:
        import playwright  # noqa: F401
    except ImportError:
        print('playwright missing: pip install playwright && playwright install chromium', file=sys.stderr)
        return 1
    out_dir = Path(out_dir)
    sky = out_dir / 'skysight'
    sky.mkdir(parents=True, exist_ok=True)
    for old in sky.glob('*.png'):
        old.unlink()  # never present old images as current ones

    def save(target, data):
        (out_dir / target['file']).write_bytes(data)

    entries = run(spot, day, todo, save, headless)
    numbers_file = sky / 'skysight_windgram.json'
    numbers_file.unlink(missing_ok=True)
    for entry in entries:
        numbers = entry.pop('numbers', None)
        if numbers:
            numbers_file.write_text(json.dumps(numbers, indent=1), encoding='utf-8')
    (sky / 'status_skysight.json').write_text(
        json.dumps({'date': day.isoformat(), 'images': entries}, indent=2, ensure_ascii=False), encoding='utf-8')
    for entry in entries:
        print(f"  {'OK    ' if entry['ok'] else 'FAILED'} {entry['file']}" +
              (f"  ({entry['reason']})" if entry['reason'] else ''))
    if pptx and any(e['ok'] for e in entries):
        from skysight_pptx import build_pptx
        name = (key or spot.get('name', 'spot')).replace(' ', '_')
        out_file = sky / f'skysight_{name}_{day.isoformat()}.pptx'
        build_pptx(todo, entries, out_dir, spot.get('name', key or ''), day, out_file)
        print(f'  PPTX: {out_file}')
    return 2 if any(not e['ok'] for e in entries) or not entries else 0


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Skysight screenshots for a weather briefing')
    parser.add_argument('--spot', required=True, help='key, name or alias from spots.yaml / spots.example.yaml')
    parser.add_argument('--date', help='YYYY-MM-DD in the local time of the spot (default: today)')
    parser.add_argument('--out', help='output directory (default: briefings/<spot>/<date>/charts)')
    parser.add_argument('--headless', action='store_true', help='no visible browser window')
    parser.add_argument('--no-pptx', action='store_true', help='images only, no PPTX')
    args = parser.parse_args()
    key, spot = find_spot(args.spot)
    if spot is None:
        print(f"Unknown spot '{args.spot}' (see spots.example.yaml)", file=sys.stderr)
        return 1
    day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(ZoneInfo(spot['timezone'])).date()
    out = Path(args.out) if args.out else ROOT / 'briefings' / key / day.isoformat() / 'charts'
    return download(spot, day, out, args.headless, not args.no_pptx, key)


if __name__ == '__main__':
    sys.exit(main())
