import unittest,base64,hashlib,struct,zlib
from project_data_service.npc_animations import validate_derived_reference
class Tests(unittest.TestCase):
 def test_exact_binding(self):
  chunk=lambda k,d:struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d)&0xffffffff)
  raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',384,384,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*1536)*384))+chunk(b'IEND',b'');sha=hashlib.sha256(raw).hexdigest();source='a'*64+'.png'
  v={'schemaVersion':'ngs.animation-derived-reference.v1','sourceAssetId':source,'sha256':sha,'bytes':len(raw),'direction':'n','providerProof':{'actualToolOutput':True,'sha256':'b'*64},'postprocess':{'profileId':'npc.pose-pixel.384.v1','sourceSha256':'b'*64},'promptHash':'c'*64,'contextHash':'d'*64,'requestId':'req.test','base64':base64.b64encode(raw).decode()}
  slot={'referenceType':'pixel','direction':'n','referenceAssetId':sha+'.png'}
  self.assertEqual(validate_derived_reference(v,slot,source,'req.test'),raw)
  for key,val in [('sourceAssetId','f'*64+'.png'),('requestId','other'),('direction','ne'),('sha256','e'*64),('providerProof',{'actualToolOutput':False})]:
   with self.assertRaises(Exception):validate_derived_reference({**v,key:val},slot,source,'req.test')
if __name__=='__main__':unittest.main()
