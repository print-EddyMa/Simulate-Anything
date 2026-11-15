# Placeholder score (used only if Higgsfield M1 is missing): 100 BPM, sections per the brief.
import numpy as np, wave, sys
SR=44100; T=125; n=SR*T; t=np.arange(n)/SR
out=np.zeros((n,2))
beat=60/100
def env(start,end,a=0.5,r=1.0):
    e=np.clip((t-start)/a,0,1)*np.clip((end-t)/r,0,1); return e
def note(f): return 440*2**((f-69)/12)
def add(sig,start,end,a=0.5,r=1.0,pan=0.0,g=1.0):
    e=env(start,end,a,r)*g
    out[:,0]+=sig*e*(1-pan)/2*2; out[:,1]+=sig*e*(1+pan)/2*2
# drone (A minor -> C major lift)
for f,g in [(note(33),0.25),(note(45),0.12),(note(52),0.06)]:
    add(np.sin(2*np.pi*f*t)+0.3*np.sin(2*np.pi*f*2.003*t),0,50,4,2,g=g)
    add(np.sin(2*np.pi*f*t),50,62,1,1,g=g*1.2)
for f,g in [(note(36),0.22),(note(48),0.12),(note(55),0.07),(note(64),0.04)]:
    add(np.sin(2*np.pi*f*t)+0.25*np.sin(2*np.pi*f*2.002*t),62,125,0.3,8,g=g)
# ticking 0-9
for k in np.arange(0.5,9,beat/2):
    i=int(k*SR); L=int(0.02*SR); out[i:i+L]+=(np.random.randn(L)*np.exp(-np.arange(L)/150)*0.08)[:,None]
# arpeggio
def arp(start,end,chords,g):
    k=start; step=beat/4; j=0
    while k<end:
        ch=chords[int((k-start)/(beat*4))%len(chords)]
        f=note(ch[j%len(ch)]); L=int(step*SR*0.95); i=int(k*SR)
        tt=np.arange(L)/SR; s=(np.sign(np.sin(2*np.pi*f*tt))*0.3+np.sin(2*np.pi*f*tt))*np.exp(-tt*9)
        if i+L<n: out[i:i+L,0]+=s*g*(0.8 if j%2 else 1); out[i:i+L,1]+=s*g*(1 if j%2 else 0.8)
        k+=step; j+=1
Am=[57,60,64,69]; F=[53,57,60,65]; C=[48,55,60,64]; G=[55,59,62,67]
arp(15,50,[Am,F,C,G],0.10); arp(62,110,[C,G,Am,F],0.12)
# kick + hat
def drums(start,end,g,hat=True):
    k=start
    while k<end:
        i=int(k*SR); L=int(0.35*SR); tt=np.arange(L)/SR
        kick=np.sin(2*np.pi*(45+90*np.exp(-tt*30))*tt)*np.exp(-tt*8)
        if i+L<n: out[i:i+L]+=(kick*g)[:,None]
        if hat:
            h=int((k+beat/2)*SR); HL=int(0.05*SR)
            if h+HL<n: out[h:h+HL]+=(np.random.randn(HL)*np.exp(-np.arange(HL)/300)*g*0.25)[:,None]
        k+=beat
drums(42,50,0.55); drums(66,110,0.4)
# hits at 9, 62, 114
for at,g in [(9,0.8),(62,0.6),(114.6,0.7)]:
    i=int(at*SR); L=int(3*SR); tt=np.arange(L)/SR
    hit=(np.sin(2*np.pi*40*tt)+0.4*np.random.randn(L)*np.exp(-tt*6))*np.exp(-tt*1.6)
    out[i:i+L]+=(hit*g)[:,None]
# fade + reverb-ish smear
out*=np.clip((T-t)/4,0,1)[:,None]
k=int(0.09*SR)
for d,g in [(k,0.3),(2*k+37,0.2),(3*k+91,0.12)]: out[d:]+=out[:-d]*g
out/=np.max(np.abs(out))*1.12
with wave.open(sys.argv[1],'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out*32767).astype('<i2').tobytes())
