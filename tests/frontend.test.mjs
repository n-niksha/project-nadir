import test from 'node:test';
import assert from 'node:assert/strict';
import { wavelengthMatch, epochList, visitGroups, stepEpoch } from '../frontend/js/playback.js';
import { Tan } from '../frontend/js/sky.js';

test('motion playback uses symmetric 2% wavelength separation and excludes invalid frames', () => {
  assert.equal(wavelengthMatch(1.5, 1.52), true);
  assert.equal(wavelengthMatch(1.5, 1.54), false);
  assert.equal(wavelengthMatch(null, 1.5), false);
  assert.equal(wavelengthMatch(1.5, 1.5305), false);
  const epochs = [
    {index:0,mjd:61000,wavelength_um:1.5,usable:true},
    {index:1,mjd:61200,wavelength_um:1.51,usable:true},
    {index:2,mjd:61100,wavelength_um:1.6,usable:true},
    {index:3,mjd:61300,wavelength_um:1.5,usable:false},
    {index:4,mjd:null,wavelength_um:1.5,usable:true},
    {index:5,mjd:61400,wavelength_um:1.5,usable:true,has_frame:false},
  ];
  assert.deepEqual(epochList({epochs},0).map(epoch=>epoch.index), [0,1]);
  assert.deepEqual(epochList({epochs},0,'spectral').map(epoch=>epoch.index), [0,2,1]);
  assert.deepEqual(epochList({epochs},99), []);
});

test('visit groups follow actual time gaps and retain unusable observations', () => {
  const epochs = [{mjd:60090},{mjd:60001},{mjd:60000,usable:false},{mjd:null}];
  const groups = visitGroups(epochs);
  assert.deepEqual(groups.map(group=>group.map(epoch=>epoch.mjd)), [[60000,60001],[60090]]);
});

test('stepping from an unmatched manual exposure chooses the next or previous real time', () => {
  const field = {epochs:[{index:0,mjd:60000,wavelength_um:1.5,usable:true},
    {index:1,mjd:60100,wavelength_um:1.8,usable:true}, {index:2,mjd:60200,wavelength_um:1.51,usable:true}]};
  assert.equal(stepEpoch(field,0,'motion',1,1),2);
  assert.equal(stepEpoch(field,0,'motion',1,-1),0);
  assert.equal(stepEpoch(field,0,'motion',2,1),0);
});

test('TAN transform round-trips near RA wrap, north is up and east is left', () => {
  const projection = new Tan({crval:[359.99,60],crpix:[50.5,50.5],cdelt:[-0.001,0.001],nx:100,ny:100});
  for (const [x,y] of [[0,0],[49.5,49.5],[90,10]]) {
    const sky = projection.pixToSky(x,y), pixel = projection.skyToPix(...sky);
    assert.ok(Math.abs(pixel[0]-x)<1e-7 && Math.abs(pixel[1]-y)<1e-7);
  }
  assert.ok(projection.skyToPix(0.001,60)[0]<49.5);
  assert.ok(projection.skyToPix(359.99,60.01)[1]>49.5);
});
