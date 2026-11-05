"""Verified reference regions, in source-image pixels, never desktop coordinates.

Source images are iRacing's official GT3/Cup manuals (see samples/gt3_dash/sources.json).
Missing tire temperature displays are deliberately empty; engine and brake data
must never be substituted for tire temperature.
"""
from pathlib import Path

ROOT=Path(__file__).resolve().parent
WHEELS=('FL','FR','RL','RR')


def regions(*boxes):return dict(zip(WHEELS,boxes))


def entry(name,image,crop,pressure,anchor,temperature=None,kind=None,**extra):
    return dict(name=name,image=image,crop=crop,pressure=pressure,anchor=anchor,
                temperature=temperature or {},temperature_kind=kind,**extra)


VEHICLES={
 'bmwm4gt3':entry('BMW M4 GT3','bmw_m4_p7.png',[590,124,1224,505],
    regions([605,171,684,210],[698,171,780,210],[605,233,684,273],[698,233,780,273]),
    [598,128,788,156]),
 'acuransxevo22gt3':entry('Acura NSX GT3 EVO 22','acura_nsx_p7.png',[600,145,1830,875],
    regions([627,377,793,443],[816,375,987,441],[624,536,793,602],[815,535,988,601]),
    [632,325,787,360]),
 'amvantageevogt3':entry('Aston Martin Vantage GT3 EVO','aston_vantage_evo_p7.png',[740,135,1470,545],
    regions([1278,358,1359,394],[1372,353,1454,392],[1277,403,1359,439],[1371,401,1455,439]),
    [1090,335,1174,366]),
 'audir8lmsevo2gt3':entry('Audi R8 LMS EVO II GT3','audi_r8_evo2_p7.png',[775,190,1730,930],
    regions([800,244,948,304],[991,244,1140,305],[799,380,947,441],[989,380,1141,441]),
    [1180,211,1302,248],
    regions([1490,367,1567,408],[1604,367,1682,408],[1488,431,1567,472],[1602,433,1683,475]),'average'),
 'chevyvettez06rgt3':entry('Chevrolet Corvette Z06 GT3.R','corvette_z06_p7.png',[450,64,1555,678],
    regions([469,78,596,131],[624,78,749,131],[470,154,596,207],[624,155,751,208]),
    [489,218,741,246],
    regions([474,251,596,306],[627,251,751,306],[473,330,598,385],[625,330,752,385]),'carcass'),
 'ferrari296gt3':entry('Ferrari 296 GT3','ferrari_296_p7.png',[458,218,1518,841],
    regions([482,422,614,479],[640,422,772,479],[482,541,614,602],[640,541,772,602]),
    [499,384,614,416],
    regions([1216,420,1338,479],[1369,420,1491,479],[1216,541,1338,602],[1369,541,1491,602]),'surface',
    temperature_anchor=[1238,385,1265,416]),
 'fordmustanggt3':entry('Ford Mustang GT3','mustang_p7.png',[664,246,1700,860],
    regions([1045,643,1173,682],[1190,643,1316,682],[1045,702,1173,741],[1190,702,1316,741]),
    [1115,592,1280,628],
    regions([1393,643,1501,682],[1530,643,1635,682],[1393,702,1501,741],[1530,702,1635,741]),'carcass'),
 'lamborghinievogt3':entry('Lamborghini Huracan GT3 EVO','lamborghini_huracan_evo_p8.png',[475,56,1313,690],
    regions([490,339,635,392],[1151,339,1297,392],[486,468,635,520],[1151,467,1297,520]),
    [849,267,955,298],
    regions([650,365,758,414],[1026,365,1138,414],[650,451,758,505],[1026,451,1138,505]),'unspecified',
    page_note='Magnus Quali'),
 'mclaren720sgt3':entry('McLaren 720S GT3 EVO','mclaren_720s_p7.png',[695,132,1504,626],
    regions([1208,245,1281,277],[1392,244,1466,276],[1208,328,1281,361],[1392,326,1466,359]),
    [1261,210,1469,240],
    regions([1208,280,1280,312],[1391,278,1466,311],[1208,363,1280,395],[1392,360,1466,393]),'carcass'),
 'mercedesamgevogt3':entry('Mercedes-AMG GT3 2020','mercedes_amg_2020_p8.png',[318,91,648,338],
    regions([325,191,373,213],[378,193,425,215],[325,266,373,288],[378,268,425,290]),
    [529,142,623,163],
    regions([325,216,373,238],[378,218,425,240],[325,241,373,263],[378,243,425,265]),'unspecified',
    page_note='2'),
 'porsche992rgt3':entry('Porsche 911 GT3 R (992)','porsche_992_gt3r_p7.png',[406,157,1641,641],
    regions([940,510,1015,544],[1032,511,1105,545],[940,553,1015,586],[1032,553,1105,587]),
    [971,475,1073,506],
    regions([887,479,944,508],[1103,481,1159,508],[887,590,944,618],[1103,590,1159,619]),'surface'),
 'porsche9922cup':entry('Porsche 911 Cup (992.2)','porsche_9922_cup_p7.jpg',[680,306,1496,624],
    regions([1028,541,1083,565],[1089,541,1144,565],[1028,571,1083,595],[1089,571,1144,595]),
    [1050,516,1120,539],
    regions([994,516,1037,548],[1141,516,1183,548],[994,589,1037,620],[1141,589,1183,620]),'surface'),
}


def canonical_car(value):
    normalized=str(value or '').strip('/\\').lower().replace('\\','/')
    return normalized if normalized in VEHICLES else None


def name_for(value):return VEHICLES.get(canonical_car(value),{}).get('name',str(value or '차량 대기'))
