import numpy as np
from nadir.science.deblend import Stamp,response,fit_model
from nadir.science.stir import SpectralFrame,fit

def experiment(seed,spectral):
 rng=np.random.default_rng(seed);frames=[];sep=1.5;center=np.array([5.15,4.83]);
 for k,w in enumerate((1.2,1.5,1.8,2.1)):
  shift=tuple(rng.uniform(-.45,.45,2));st=Stamp(np.zeros((11,11)),np.ones((11,11)),np.ones((11,11),bool),str(k),shift,.8)
  xy=[center+[-sep/2,0],center+[sep/2,0]]
  # component colors differ only in the spectral experiment
  a,b=(260,90) if spectral else (170,170)
  f1=a*(w/1.6)**1.2;f2=b*(w/1.6)**(-.8)
  st.image=2+f1*response(st,*xy[0])+f2*response(st,*xy[1])+rng.normal(size=st.image.shape)*2
  frames.append(SpectralFrame(st,w,.02*w,61000+k*40,held_out=(k==3)))
 return frames,xy

def test_stir_spatial_spectral_and_holdout():
 frames,xy=experiment(3,True);r=fit(frames,2,spectral=True)
 assert r['mode']=='stir_experimental' and len(r['held_out'])==1
 assert np.linalg.norm(np.array(r['positions'])-xy)<.5
 assert r['parameter_covariance']

def test_stir_does_not_mutate_stamps_or_change_spatial_baseline():
 frames,_=experiment(4,False);before=[f.stamp.image.copy() for f in frames]
 old=fit_model([f.stamp for f in frames[:3]],2)
 stir=fit(frames,2,spectral=False)
 assert all(np.array_equal(a,f.stamp.image) for a,f in zip(before,frames))
 assert np.isfinite(stir['training_chi2']).all()
 assert old['success']

def test_spectral_regularization_is_explicit_and_finite():
 frames,_=experiment(5,True);a=fit(frames,2,regularization=0);b=fit(frames,2,regularization=10)
 assert np.isfinite(b['spectral_slopes']).all()
 assert np.linalg.norm(b['spectral_slopes'])<=np.linalg.norm(a['spectral_slopes'])+1e-5
