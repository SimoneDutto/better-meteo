/** Shared deterministic paths for the offline static exporter and HTML links. */
export function basePath(value = '/') {
  if (!/^\/(?:[a-zA-Z0-9._~-]+\/)*[a-zA-Z0-9._~-]*\/?$/.test(value) || value.split('/').some(p => p === '..' || p === '.')) {
    throw Error('PAGES_BASE must be a URL path such as /better-meteo/ or /');
  }
  return value === '/' ? '/' : `/${value.split('/').filter(Boolean).join('/')}/`;
}

export function pagePath(state) {
  if (!/^[a-z0-9-]{1,40}$/.test(state.city)) throw Error('Invalid city id');
  const tabs = ['forecast', 'outlook', 'pressure', 'rainfall', 'wind', 'climate', 'sources'];
  if (!tabs.includes(state.tab)) throw Error('Invalid tab');
  const parts = [state.city, state.tab];
  if (state.tab === 'pressure') {
    if (state.step != null) {
      if (!/^\d+$/.test(String(state.step))) throw Error('Invalid pressure step');
      parts.push(`at-${state.step}`);
    }
    if (state.motion === 'on') parts.push('loop');
  }
  if (state.tab === 'climate' && state.view && state.view !== 'overview') {
    if (!['periods', 'charts', 'records', 'method'].includes(state.view)) throw Error('Invalid climate view');
    parts.push(state.view);
    if (state.view === 'records') parts.push(`page-${Math.max(0,Number.parseInt(state.page || '0',10) || 0)}`);
  }
  if (state.cycle === '1') parts.push('rotate');
  return parts.join('/') + '/';
}

export function pageUrl(base, state) { return basePath(base) + pagePath(state); }

export function pageStates(config, pressure, readCity) {
  const states = [];
  for (const city of config.cities) {
    const common = { city: city.id };
    for (const tab of ['forecast','outlook','pressure','rainfall','wind','climate','sources']) states.push({...common,tab});
    for (const frame of pressure?.data?.frames || []) {
      states.push({...common,tab:'pressure',step:String(frame.step)});
      if (pressure.data.animation) states.push({...common,tab:'pressure',step:String(frame.step),motion:'on'});
    }
    for (const view of ['periods','charts','method']) states.push({...common,tab:'climate',view});
    const years = (readCity(city.id, 'climate')?.data?.annual || []).filter(r=>r.high!=null||r.low!=null||r.precipitation!=null).length;
    for (let page=0;page<Math.max(1,Math.ceil(years/10));page++) states.push({...common,tab:'climate',view:'records',page:String(page)});
  }
  return states.flatMap(state=>[state,{...state,cycle:'1'}]);
}
