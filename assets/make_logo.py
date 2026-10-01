from PIL import Image, ImageDraw, ImageFont
import math
"""Regenerates assets/dwanilive.ico + .png and static/favicon.png:
gradient ध in Rozha One with a "LIVE" subscript in Mukta ExtraBold, matching the
app wordmark. Fonts (SIL OFL) are fetched from github.com/google/fonts.
Run: python assets/make_logo.py   (needs Pillow with raqm)"""
import os, urllib.request
from pathlib import Path
HERE = Path(__file__).resolve().parent
F = str(HERE / ".fonts") + "/"
os.makedirs(F, exist_ok=True)
for rel in ("ofl/rozhaone/RozhaOne-Regular.ttf", "ofl/mukta/Mukta-ExtraBold.ttf"):
    dst = F + rel.split("/")[-1]
    if not os.path.exists(dst):
        urllib.request.urlretrieve("https://raw.githubusercontent.com/google/fonts/main/" + rel, dst)
S=1024
BG=(0xfd,0xf6,0xe7); EDGE=(0xf4,0xe9,0xcf); INK_DIM=(0x5c,0x44,0x33)
STOPS=[(0,(0xa8,0x46,0x0c)),(0.5,(0xc8,0x1e,0x5e)),(1,(0x22,0x33,0x67))]

def gradient(w,h):
    g=Image.new("RGB",(w,h)); px=g.load()
    vx,vy=math.sin(math.radians(120)),-math.cos(math.radians(120))
    L=abs(w*vx)+abs(h*vy)
    for y in range(h):
        for x in range(w):
            t=((x-w/2)*vx+(y-h/2)*vy)/L+0.5
            t=min(1,max(0,t))
            for i in range(len(STOPS)-1):
                a,ca=STOPS[i]; b,cb=STOPS[i+1]
                if t<=b:
                    u=(t-a)/(b-a); px[x,y]=tuple(int(ca[k]+(cb[k]-ca[k])*u) for k in range(3)); break
    return g



def glyph_mask(text,font,size_px):
    f=ImageFont.truetype(font,size_px,layout_engine=ImageFont.Layout.RAQM)
    m=Image.new("L",(S*2,S*2),0); d=ImageDraw.Draw(m)
    d.text((S//2,S//2),text,font=f,fill=255,language="hi" if text=="ध" else None)
    return m.crop(m.getbbox())

def make(with_live):
    img=Image.new("RGBA",(S,S),(0,0,0,0))
    tile=Image.new("L",(S,S),0); ImageDraw.Draw(tile).rounded_rectangle([24,24,S-24,S-24],radius=230,fill=255)
    base=Image.new("RGBA",(S,S),BG+(255,))
    img.paste(base,(0,0),tile)
    ImageDraw.Draw(img).rounded_rectangle([24,24,S-24,S-24],radius=230,outline=EDGE+(255,),width=14)
    dh=glyph_mask("ध",F+"RozhaOne-Regular.ttf",900 if with_live else 1000)
    inner=int(S*0.74)  # keep everything well inside the rounded tile
    target_h=int(S*(0.50 if with_live else 0.58))
    dh=dh.resize((int(dh.width*target_h/dh.height),target_h),Image.LANCZOS)
    if with_live:
        live=glyph_mask("LIVE",F+"Mukta-ExtraBold.ttf",220)
        lw=int(S*0.24); live=live.resize((lw,int(live.height*lw/live.width)),Image.LANCZOS)
        gap=int(S*0.015)
        total_w=dh.width+gap+live.width
        if total_w>inner:  # shrink both proportionally to fit
            k=inner/total_w
            dh=dh.resize((int(dh.width*k),int(dh.height*k)),Image.LANCZOS)
            live=live.resize((int(live.width*k),int(live.height*k)),Image.LANCZOS)
            gap=int(gap*k); total_w=dh.width+gap+live.width
        x0=(S-total_w)//2; y0=(S-dh.height)//2 - int(S*0.02)
        img.paste(gradient(dh.width,dh.height),(x0,y0),dh)  # gradient spans the glyph, like CSS background-clip:text
        lx=x0+dh.width+gap; ly=y0+dh.height-live.height  # subscript: baseline-aligned to bottom of ध
        img.paste(Image.new("RGBA",live.size,INK_DIM+(255,)),(lx,ly),live)
    else:
        if dh.width>inner:
            k=inner/dh.width; dh=dh.resize((inner,int(dh.height*k)),Image.LANCZOS)
        x0=(S-dh.width)//2; y0=(S-dh.height)//2
        img.paste(gradient(dh.width,dh.height),(x0,y0),dh)  # gradient spans the glyph, like CSS background-clip:text
    return img

full=make(True); small=make(False)
full.save(HERE / "dwanilive.png")
full.resize((128, 128), Image.LANCZOS).save(HERE.parent / "static" / "favicon.png", optimize=True)
full.resize((180, 180), Image.LANCZOS).save(HERE.parent / "static" / "apple-touch-icon.png", optimize=True)
sizes={}
for s in (16,24,32,48,64,128,256):
    sizes[s]=(small if s<=32 else full).resize((s,s),Image.LANCZOS)
sizes[256].save(HERE / "dwanilive.ico",format="ICO",sizes=[(s,s) for s in sizes],append_images=[sizes[s] for s in sizes if s!=256])


