import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { apiFetch } from '../services/api';
import { toast } from '../utils/toast';

type Source = 'nyaa' | 'leetx' | 'animetosho';
interface Result { title: string; size?: string; seeders?: number; leechers?: number; magnet?: string; torrent_id?: number }
interface Torrent { id: number; name: string; progress: number; status: string; size_str: string; done: boolean; eta: string }
const SOURCES: { id: Source; label: string }[] = [
  { id: 'nyaa', label: 'Nyaa' }, { id: 'leetx', label: '1337x' }, { id: 'animetosho', label: 'AnimeTosho' },
];

export default function Torrents() {
  const { me } = useAuth();
  const [source, setSource] = useState<Source>('nyaa');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [spanishOnly, setSpanishOnly] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const [torrents, setTorrents] = useState<Torrent[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch('/torrents');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Transmission no disponible');
      setTorrents(data.torrents || []);
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Transmission no disponible', 'error', 4000);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 10000);
    return () => window.clearInterval(timer);
  }, [load]);

  if (me?.role !== 'admin') return <div className="p-5 text-sm text-nf-text3">Solo administradores.</div>;

  const search = async (nextPage = 1) => {
    if (query.trim().length < 2) return;
    setBusy(true);
    setPage(nextPage);
    try {
      const params = new URLSearchParams({ query: query.trim(), page: String(nextPage) });
      if (source !== 'leetx') params.set('spanish_only', String(spanishOnly));
      const response = await apiFetch(`/${source}/search?${params}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Error de búsqueda');
      setResults(data.results || []);
      if (!data.results?.length) toast('Sin resultados', 'info');
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Error de búsqueda', 'error', 4000);
      setResults([]);
    } finally {
      setBusy(false);
    }
  };

  const add = async (item: Result) => {
    try {
      let magnet = item.magnet || '';
      if (!magnet && source === 'leetx' && item.torrent_id) {
        const response = await apiFetch(`/leetx/magnet?id=${item.torrent_id}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'No se obtuvo el magnet');
        magnet = data.magnet;
      }
      const response = await apiFetch('/torrents', { method: 'POST', body: JSON.stringify({ magnet, title: item.title }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo añadir');
      toast('Torrent añadido', 'ok');
      load();
    } catch (error) {
      toast(error instanceof Error ? error.message : 'No se pudo añadir', 'error', 4000);
    }
  };

  const action = async (torrent: Torrent, actionName: 'pause' | 'resume' | 'remove') => {
    if (actionName === 'remove' && !confirm(`¿Eliminar "${torrent.name}" y sus datos?`)) return;
    try {
      const response = await apiFetch(actionName === 'remove' ? `/torrent/${torrent.id}` : `/torrent/${actionName}`, {
        method: actionName === 'remove' ? 'DELETE' : 'POST',
        ...(actionName !== 'remove' ? { body: JSON.stringify({ torrent_id: torrent.id }) } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo completar la acción');
      load();
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Error de Transmission', 'error', 4000);
    }
  };

  return (
    <div className="px-4 pb-8 pt-[calc(var(--safe-t)+16px)]">
      <h1 className="mb-1 text-2xl font-bold">Torrents</h1>
      <p className="mb-5 text-sm text-nf-text3">Fuentes externas y Transmission</p>

      <div className="mb-4 flex gap-2 overflow-x-auto">
        {SOURCES.map(item => (
          <button key={item.id} onClick={() => { setSource(item.id); setResults([]); setPage(1); }}
            className={`shrink-0 rounded-full px-3 py-2 text-xs ${source === item.id ? 'bg-white text-black' : 'bg-white/10 text-nf-text2'}`}>
            {item.label}
          </button>
        ))}
      </div>
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); search(1); }}>
        <input value={query} onChange={e => setQuery(e.target.value)} minLength={2}
          placeholder={`Buscar en ${SOURCES.find(s => s.id === source)?.label}`}
          className="min-w-0 flex-1 rounded-xl bg-white/10 px-3 py-3 text-[15px] outline-none focus:bg-white/15" />
        <button disabled={busy || query.trim().length < 2} className="rounded-xl bg-white px-4 text-sm font-semibold text-black disabled:opacity-50">
          {busy ? '…' : 'Buscar'}
        </button>
      </form>
      {source !== 'leetx' && <label className="mt-3 flex items-center gap-2 text-xs text-nf-text3">
        <input type="checkbox" checked={spanishOnly} onChange={e => setSpanishOnly(e.target.checked)} /> Solo subtítulos en español
      </label>}

      <div className="mt-3 divide-y divide-white/10">
        {results.map((item, index) => (
          <article key={`${item.torrent_id || item.magnet || item.title}-${index}`} className="py-3">
            <p className="break-words text-sm font-medium">{item.title}</p>
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="text-[11px] text-nf-text3">{item.size || '?'} · ↑{item.seeders || 0} ↓{item.leechers || 0}</span>
              <button onClick={() => add(item)} className="shrink-0 rounded-full bg-white/15 px-3 py-2 text-[11px] font-medium">Añadir</button>
            </div>
          </article>
        ))}
      </div>
      {results.length > 0 && <div className="my-4 flex items-center justify-center gap-4">
        <button disabled={page <= 1 || busy} onClick={() => search(page - 1)} className="text-sm disabled:opacity-30">Anterior</button>
        <span className="text-xs text-nf-text3">{page}</span>
        <button disabled={busy} onClick={() => search(page + 1)} className="text-sm">Siguiente</button>
      </div>}

      <h2 className="mb-3 mt-8 text-base font-semibold">Transmission</h2>
      {torrents.length === 0 && <p className="text-xs text-nf-text3">No hay torrents o Transmission no está disponible.</p>}
      <div className="space-y-3">
        {torrents.map(torrent => <article key={torrent.id} className="rounded-xl bg-white/[0.06] p-3">
          <p className="break-words text-sm font-medium">{torrent.name}</p>
          <p className="mt-1 text-[11px] text-nf-text3">{torrent.size_str} · {torrent.status}{torrent.eta ? ` · ${torrent.eta}` : ''}</p>
          <div className="my-3 h-1 overflow-hidden rounded bg-white/10">
            <div className="h-full bg-nf-red" style={{ width: `${Math.max(0, Math.min(100, torrent.progress))}%` }} />
          </div>
          <div className="flex gap-2">
            <button onClick={() => action(torrent, torrent.status === 'stopped' ? 'resume' : 'pause')}
              className="rounded bg-white/10 px-3 py-2 text-xs">{torrent.status === 'stopped' ? 'Reanudar' : 'Pausar'}</button>
            <button onClick={() => action(torrent, 'remove')} className="rounded bg-nf-red/25 px-3 py-2 text-xs">Eliminar</button>
          </div>
        </article>)}
      </div>
    </div>
  );
}
