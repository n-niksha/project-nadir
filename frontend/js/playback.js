export function wavelengthMatch(first, second, tolerance = 0.02) {
  return Number.isFinite(first) && Number.isFinite(second) && first > 0 && second > 0 &&
    Math.abs(first - second) / ((first + second) / 2) <= tolerance;
}

export function epochList(field, anchor, mode = "motion") {
  const wavelength = field?.epochs.find((epoch) => epoch.index === anchor)?.wavelength_um;
  return (field?.epochs || []).filter((epoch) => epoch.usable && epoch.has_frame !== false && Number.isFinite(epoch.mjd) &&
    (mode === "spectral" || wavelengthMatch(epoch.wavelength_um, wavelength)))
    .sort((first, second) => first.mjd - second.mjd || first.index - second.index);
}

export function visitGroups(epochs, gapDays = 30) {
  const groups = [];
  for (const epoch of epochs.filter((item) => Number.isFinite(item.mjd)).slice().sort((first, second) => first.mjd - second.mjd)) {
    const last = groups.at(-1);
    if (!last || epoch.mjd - last.at(-1).mjd > gapDays) groups.push([epoch]);
    else last.push(epoch);
  }
  return groups;
}

export function stepEpoch(field, anchor, mode, current, direction) {
  const list = epochList(field, anchor, mode);
  if (!list.length) return null;
  const position = list.findIndex(epoch => epoch.index === current);
  if (position >= 0) return list[(position + direction + list.length) % list.length].index;
  const mjd = field.epochs.find(epoch => epoch.index === current)?.mjd;
  return (direction > 0 ? list.find(epoch => epoch.mjd > mjd) || list[0]
    : list.findLast(epoch => epoch.mjd < mjd) || list.at(-1)).index;
}
