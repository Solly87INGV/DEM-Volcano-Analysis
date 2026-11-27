"""D8 upstream-catchment tracer (pysheds ESRI direction codes: 64 N, 128 NE, 1 E, 2 SE, 4 S, 8 SW, 16 W, 32 NW).
Used by valles_divide.py; written because pysheds 0.5 catchment() fails with numpy 2."""
import numpy as np
from collections import deque
DIRS={64:(-1,0),128:(-1,1),1:(0,1),2:(1,1),4:(1,0),8:(1,-1),16:(0,-1),32:(-1,-1)}
def downstream(fd):
    H,W=fd.shape; ds=np.full(H*W,-1,dtype=np.int64)
    rr,cc=np.indices((H,W))
    for code,(dr,dc) in DIRS.items():
        m=fd==code; r2=rr[m]+dr; c2=cc[m]+dc; ok=(r2>=0)&(r2<H)&(c2>=0)&(c2<W)
        idx=(rr[m]*W+cc[m])[ok]; ds[idx]=r2[ok]*W+c2[ok]
    return ds
def catchment(fd,points):
    H,W=fd.shape; ds=downstream(fd)
    src=np.nonzero(ds>=0)[0]; order=np.argsort(ds[src]); src=src[order]; tgt=ds[src]
    starts=np.searchsorted(tgt,np.arange(H*W)); ends=np.searchsorted(tgt,np.arange(H*W),side='right')
    seen=np.zeros(H*W,bool); q=deque()
    for r,c in points: i=r*W+c; seen[i]=True; q.append(i)
    while q:
        i=q.popleft()
        for j in src[starts[i]:ends[i]]:
            if not seen[j]: seen[j]=True; q.append(j)
    return seen.reshape(H,W)
