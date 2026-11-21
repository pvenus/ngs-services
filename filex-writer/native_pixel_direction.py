"""Pure validation for the detached direction-only pixel publication."""
import math
import re
import struct
import zlib


NAMESPACE = 'animation-processed-directions/'
SCHEMA = 'ngs.animation-pixel-direction-candidate.v1'
WORKFLOW_SCHEMA = 'workflow.animation-pixel-direction.v1'
PATH = re.compile(NAMESPACE + r'(representative|combat|idle)/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/(n|ne|e|se|s|sw|w|nw)\.json')
IDENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}')
HASH = re.compile(r'[0-9a-f]{64}')
PNG = re.compile(r'[0-9a-f]{64}\.png')
KEYS = {'schemaVersion', 'requestId', 'npcId', 'generationId', 'posePreset', 'presetId', 'direction',
        'rawRetention', 'canvas', 'pixelPolicy', 'frames', 'rawManifestHash', 'alphaManifestHash',
        'sourceBindingHash', 'normalizationHash', 'frameCount', 'fps'}
SCALARS = {'width', 'height', 'sourceWidth', 'sourceHeight', 'paddingPixels', 'marginRatio', 'scale',
           'scaleX', 'scaleY', 'sourceCanvasWidth', 'sourceCanvasHeight', 'offsetX', 'offsetY'}
POLICY = {'pixelScale': .5, 'resizeBeforeQuantization': True, 'resizeFilter': 'BOX', 'colors': 32,
          'alphaThreshold': 128, 'dither': False, 'paletteScope': 'preset-frozen-shared'}
LINEAGE = ('npcId', 'generationId', 'posePreset', 'presetId', 'direction', 'sourceBindingHash', 'frameCount', 'fps')
FRAME_KEYS = {'index', 'assetId', 'rawAssetId', 'alphaAssetId', 'sourceBounds', 'sourceTimestamp'}
RETENTION = 'retain-native-sequence-independently'


def require(ok, code):
    if not ok:
        raise ValueError('direction pixel ' + code)


def finite(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate(owner, relative, body, *, workflow=False):
    m = PATH.fullmatch(relative)
    require(m and type(body) is dict and set(body) == KEYS and body['schemaVersion'] == (WORKFLOW_SCHEMA if workflow else SCHEMA),
            'manifest')
    require((body['npcId'], body['posePreset'], body['presetId'], body['direction']) == (owner, *m.groups())
            and all(matches(IDENT, body[k]) for k in ('requestId', 'generationId'))
            and body['rawRetention'] == RETENTION
            and all(matches(HASH, body[k]) for k in ('rawManifestHash', 'alphaManifestHash',
                                                     'sourceBindingHash', 'normalizationHash')), 'binding')
    canvas, policy = body['canvas'], body['pixelPolicy']
    require(type(canvas) is dict and set(canvas) == SCALARS | {'unionBounds', 'pivot'}
            and all(finite(canvas[k]) for k in SCALARS) and canvas['scale'] == .5
            and all(type(canvas[k]) is int and canvas[k] > 0
                    for k in ('width', 'height', 'sourceWidth', 'sourceHeight'))
            and type(canvas['unionBounds']) is list and len(canvas['unionBounds']) == 4
            and all(finite(v) for v in canvas['unionBounds'])
            and (workflow or canvas['unionBounds'] == [0, 0, canvas['sourceWidth'], canvas['sourceHeight']])
            and type(canvas['pivot']) is dict and set(canvas['pivot']) == {'x', 'y'}
            and all(finite(v) for v in canvas['pivot'].values()), 'canvas')
    w, h = canvas['sourceWidth'], canvas['sourceHeight']
    width, height = canvas['width'], canvas['height']
    if workflow:
        l,t,r,b=canvas['unionBounds']
        require(all(type(v) is int for v in (l,t,r,b)) and 0<=l<r<=w and 0<=t<b<=h,'workflow bounds')
        pad=max(4,math.ceil(max(r-l,b-t)*.1))
        cw,ch=r-l+2*pad,b-t+2*pad
        sx,sy=width/cw,height/ch
        require((width,height)==((cw+1)//2,(ch+1)//2)
                and (canvas['sourceCanvasWidth'],canvas['sourceCanvasHeight'])==(cw,ch)
                and canvas['marginRatio']==.1 and canvas['paddingPixels']==math.ceil(pad*.5)
                and (canvas['scaleX'],canvas['scaleY'])==(sx,sy)
                and math.isclose(canvas['offsetX'],(pad-l)*sx,abs_tol=1e-9)
                and math.isclose(canvas['offsetY'],(pad-t)*sy,abs_tol=1e-9)
                and math.isclose(canvas['pivot']['x'],(w/2-l+pad)*sx,abs_tol=1e-9)
                and math.isclose(canvas['pivot']['y'],(b-t+pad)*sy,abs_tol=1e-9), 'workflow canvas')
    else:
        require((width, height) == ((w + 1) // 2, (h + 1) // 2)
                and (canvas['sourceCanvasWidth'], canvas['sourceCanvasHeight']) == (w, h)
                and (canvas['scaleX'], canvas['scaleY']) == (width / w, height / h)
                and all(canvas[k] == 0 for k in ('paddingPixels', 'marginRatio', 'offsetX', 'offsetY'))
                and 0 <= canvas['pivot']['x'] <= width and 0 <= canvas['pivot']['y'] <= height, 'scaled canvas')
    fixed={**POLICY,'paletteScope':'all-frames-in-direction'} if workflow else POLICY
    require(type(policy) is dict and set(policy) == set(fixed) | {'palette'}
            and all(type(policy[k]) is type(v) and policy[k] == v for k, v in fixed.items())
            and type(policy['palette']) is list and len(policy['palette']) == 96
            and all(type(x) is int and 0 <= x <= 255 for x in policy['palette']), 'policy')
    frames, count, fps = body['frames'], body['frameCount'], body['fps']
    require(type(frames) is list and type(count) is int and 2 <= count <= 1000 and len(frames) == count
            and finite(fps) and 0 < fps <= 120, 'timing')
    for i, frame in enumerate(frames):
        require(type(frame) is dict and set(frame) == FRAME_KEYS and type(frame['index']) is int
                and frame['index'] == i and all(matches(PNG, frame[k])
                for k in ('assetId', 'rawAssetId', 'alphaAssetId'))
                and finite(frame['sourceTimestamp']) and frame['sourceTimestamp'] == i / fps
                and type(frame['sourceBounds']) is list and len(frame['sourceBounds']) == 4
                and all(type(v) is int for v in frame['sourceBounds'])
                and (0 <= frame['sourceBounds'][0] < frame['sourceBounds'][2] <= w if workflow else
                     0 < frame['sourceBounds'][0] < frame['sourceBounds'][2] < w)
                and (0 <= frame['sourceBounds'][1] < frame['sourceBounds'][3] <= h if workflow else
                     0 < frame['sourceBounds'][1] < frame['sourceBounds'][3] < h), 'frame')
    if workflow:
        boxes=[f['sourceBounds'] for f in frames]
        require(canvas['unionBounds']==[min(b[0] for b in boxes),min(b[1] for b in boxes),
                max(b[2] for b in boxes),max(b[3] for b in boxes)], 'workflow union')
    return frames


def validate_basis(owner, relative, body, before, digest):
    """Initial write only, under the same lock as exact output CAS."""
    frames = validate(owner, relative, body)
    suffix = relative.removeprefix(NAMESPACE)
    raw = before.get(owner + '/animation-sequences/' + suffix)
    alpha = before.get(owner + '/animation-alpha/' + suffix)
    require(type(raw) is dict and raw.get('schemaVersion') == 'ngs.animation-native-png-sequence.v1'
            and type(alpha) is dict and alpha.get('schemaVersion') == 'ngs.animation-alpha-output.v1'
            and digest(raw) == body['rawManifestHash'] == alpha.get('rawManifestHash')
            and digest(alpha) == body['alphaManifestHash']
            and all(raw.get(k) == alpha.get(k) == body[k] for k in LINEAGE)
            and all(raw.get(k) == alpha.get(k) == body['canvas'][v]
                    for k, v in (('width', 'sourceWidth'), ('height', 'sourceHeight'))), 'source lineage')
    rf, af = raw.get('frames'), alpha.get('frames')
    require(type(rf) is list and type(af) is list and len(rf) == len(af) == len(frames), 'source frames')
    for i, (pixel, source, matte) in enumerate(zip(frames, rf, af)):
        require(type(source) is dict and type(matte) is dict and source.get('index') == matte.get('index') == i
                and source.get('assetId') == pixel['rawAssetId'] == matte.get('rawAssetId')
                and pixel['alphaAssetId'] == matte.get('alphaAssetId'), 'source frame lineage')


def validate_png_canvas(data, canvas):
    """Authenticate the PNG's declared geometry without decoding it in Filex."""
    require(len(data) >= 33 and data[:16] == b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            and struct.unpack('>II', data[16:24]) == (canvas['width'], canvas['height'])
            and zlib.crc32(data[12:29]) == struct.unpack('>I', data[29:33])[0], 'asset canvas')
