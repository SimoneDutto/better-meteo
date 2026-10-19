"""Render cached fields locally; never downloads coastlines, tiles, or fonts."""
import os
from pathlib import Path

# Deliberately schematic coastlines for geographic orientation, authored locally.
COASTS = [
 [(-10,36),(-9,39),(-9,43),(-2,44),(-1,46),(-5,48),(-4,49),(1,51),(4,52),(8,54),(8,57),(11,58),(13,55),(15,55),(18,60),(25,65)],
 [(-10,36),(-5,36),(-1,38),(0,41),(3,43),(6,43),(8,44),(10,44),(10,42),(12,40),(15,38),(16,38),(17,40),(16,42),(14,42),(13,44),(13,45),(15,45),(17,43),(20,40),(23,38),(24,40),(26,41),(29,41),(30,40),(35,36)],
 [(-6,50),(-5,52),(-5,55),(-6,58),(-3,59),(-2,56),(0,53),(1,51),(-3,50),(-6,50)],
 [(-10,52),(-10,55),(-7,55),(-6,53),(-7,52),(-10,52)],
 [(-17,30),(-10,31),(-6,35),(-2,36),(3,37),(8,37),(11,34),(15,33),(20,32),(25,31),(32,31),(35,33)],
 [(8,41),(9,41),(9,43),(8,43),(8,41)],[(8,39),(9,39),(10,41),(8,41),(8,39)],
 [(12,38),(15,38),(15,37),(13,37),(12,38)],
]

def validate_dependencies():
    os.environ.setdefault('MPLCONFIGDIR','/tmp/better-meteo-mpl')
    import numpy
    import matplotlib
    from PIL import Image

def render_animation(paths, path, labels):
    """Loop actual forecast snapshots; no interpolated weather or network access."""
    from PIL import Image, ImageDraw, ImageFont
    if len(paths)<2: raise ValueError('Animation needs at least two forecast snapshots')
    if len(labels)!=len(paths) or any(not label.strip() for label in labels):
        raise ValueError('Every animation frame needs a date label')
    os.environ.setdefault('MPLCONFIGDIR','/tmp/better-meteo-mpl')
    import matplotlib
    font_path=Path(matplotlib.get_data_path())/'fonts/ttf/DejaVuSans-Bold.ttf'
    frames=[]
    temporary=Path(path).with_suffix('.tmp')
    try:
        for source,label in zip(paths,labels):
            with Image.open(source) as image:
                # A separate banner stays readable even when the map is scaled down.
                canvas=Image.new('L',(image.width,image.height+96),'white')
                canvas.paste(image.convert('L'),(0,96))
                draw=ImageDraw.Draw(canvas)
                font=ImageFont.truetype(str(font_path),max(8,min(40,image.width//40)))
                draw.text((image.width//2,48),label,font=font,fill=0,anchor='mm')
                draw.rectangle((0,94,image.width-1,95),fill=0)
                # L -> P gives every frame the same fixed grayscale palette.
                frames.append(canvas.convert('P'))
        if len({frame.size for frame in frames})!=1:
            raise ValueError('Animation snapshots must have identical dimensions')
        frames[0].save(temporary,format='GIF',save_all=True,append_images=frames[1:],
            duration=[1200]*(len(frames)-1)+[2400],loop=0,disposal=2,optimize=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        for frame in frames: frame.close()

def render_fields(fields, path, extent, valid):
    import numpy as np
    os.environ.setdefault('MPLCONFIGDIR','/tmp/better-meteo-mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    gh,msl,temperature = (fields[key] for key in ('height','pressure','temperature'))
    fig,axes = plt.subplots(1,2,figsize=(12,6),dpi=140)
    for ax in axes:
        for line in COASTS: ax.plot(*zip(*line),color='0.45',linewidth=.8)
        ax.set(xlim=extent[:2],ylim=extent[2:],xlabel='Longitude E',ylabel='Latitude N')
        ax.grid(alpha=.2)
        ax.plot(7.6869,45.0703,'ko',markersize=4)
        ax.annotate('Torino',(7.6869,45.0703),xytext=(4,5),textcoords='offset points',fontsize=9)
    contour = axes[0].contour(gh[0],gh[1],gh[2]/10,levels=np.arange(480,630,6),colors='black',linewidths=1)
    axes[0].clabel(contour,fontsize=8,fmt='%d')
    pressure = axes[0].contour(msl[0],msl[1],msl[2],levels=np.arange(960,1060,5),colors='0.4',linewidths=.8,linestyles='dashed')
    axes[0].clabel(pressure,fontsize=7,fmt='%d')
    axes[0].set_title('500 hPa height (dam) / dashed sea-level pressure (hPa)',fontsize=10)
    mesh = axes[1].contourf(temperature[0],temperature[1],temperature[2],levels=np.arange(-20,37,4),cmap='Greys',extend='both')
    contour = axes[1].contour(temperature[0],temperature[1],temperature[2],levels=np.arange(-20,37,4),colors='black',linewidths=.5)
    axes[1].clabel(contour,fontsize=8,fmt='%d')
    axes[1].set_title('850 hPa temperature (°C) · darker = warmer',fontsize=10)
    fig.colorbar(mesh,ax=axes[1],shrink=.65)
    fig.suptitle(f'ECMWF IFS via Open-Meteo · valid {valid:%d %b %Y %H:%M UTC}')
    fig.text(.5,.01,'Open-Meteo / ECMWF · CC BY 4.0 · coarse sampled grid · schematic coastlines · model guidance',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.03,1,.95))
    temporary = Path(path).with_suffix('.tmp')
    fig.savefig(temporary,format='png',facecolor='white'); plt.close(fig)
    temporary.replace(path)
