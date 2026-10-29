import { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import Shell from '../components/Shell';
import Button from '../components/ui/Button';
import { useAuth } from '../contexts/AuthContext';
import { apiFetch } from '../services/api';

type Source = 'nyaa' | 'leetx' | 'animetosho';
interface Result {
  title: string;
  size?: string;
  pub_date?: string;
  seeders?: number;
  leechers?: number;
  magnet?: string;
  torrent_id?: number;
}
interface Torrent {
  id: number;
  name: string;
  progress: number;
  status: string;
  size_str: string;
  done: boolean;
  eta: string;
  ratio: number;
  peers_connected: number;
}

const SOURCE_LABELS: Record<Source, string> = {
  nyaa: 'Nyaa.si', leetx: '1337x', animetosho: 'AnimeTosho',
};

export default function Torrents() {
  const { isAdmin } = useAuth();
  const [source, setSource] = useState<Source>('nyaa');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [spanishOnly, setSpanishOnly] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const [torrents, setTorrents] = useState<Torrent[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const loadTorrents = useCallback(async () => {
    try {
      const response = await apiFetch('/torrents');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Transmission no disponible');
      setTorrents(data.torrents || []);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Transmission no disponible');
    }
  }, []);

  useEffect(() => {
    loadTorrents();
    const timer = window.setInterval(loadTorrents, 10000);
    return () => window.clearInterval(timer);
  }, [loadTorrents]);

  if (!isAdmin) return <Navigate to="/" replace />;

  const search = async (nextPage = 1) => {
    if (query.trim().length < 2) return;
    setBusy(true);
    setNotice('');
    setPage(nextPage);
    try {
      const params = new URLSearchParams({ query: query.trim(), page: String(nextPage) });
      if (source !== 'leetx') params.set('spanish_only', String(spanishOnly));
      const response = await apiFetch(`/${source}/search?${params}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Error de búsqueda');
      setResults(data.results || []);
      if (!data.results?.length) setNotice('Sin resultados');
    } catch (error) {
      setResults([]);
      setNotice(error instanceof Error ? error.message : 'Error de búsqueda');
    } finally {
      setBusy(false);
    }
  };

  const add = async (item: Result) => {
    setNotice('Añadiendo torrent…');
    try {
      let magnet = item.magnet || '';
      if (!magnet && source === 'leetx' && item.torrent_id) {
        const response = await apiFetch(`/leetx/magnet?id=${item.torrent_id}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'No se pudo obtener el magnet');
        magnet = data.magnet;
      }
      const response = await apiFetch('/torrents', {
        method: 'POST', body: JSON.stringify({ magnet, title: item.title }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo añadir');
      setNotice('Torrent añadido');
      loadTorrents();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'No se pudo añadir');
    }
  };

  const action = async (torrent: Torrent, method: 'pause' | 'resume' | 'remove') => {
    if (method === 'remove' && !window.confirm(`¿Eliminar «${torrent.name}» y sus datos?`)) return;
    const url = method === 'remove' ? `/torrent/${torrent.id}` : `/torrent/${method}`;
    try {
      const response = await apiFetch(url, {
        method: method === 'remove' ? 'DELETE' : 'POST',
        ...(method !== 'remove' ? { body: JSON.stringify({ torrent_id: torrent.id }) } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'No se pudo completar la acción');
      await loadTorrents();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Error en Transmission');
    }
  };

  return (
    <Shell>
      <div className="mx-auto max-w-6xl px-gutter pt-8">
        <h1 className="text-page font-bold">Torrents</h1>
        <p className="mt-1 text-base text-nf-dim">Búsqueda externa y cola de Transmission</p>

        <section className="mt-7 rounded-panel border border-nf-line bg-nf-surface p-5">
          <div className="flex flex-wrap gap-2">
            {(Object.keys(SOURCE_LABELS) as Source[]).map(key => (
              <button key={key} onClick={() => { setSource(key); setResults([]); setPage(1); }}
                className={`rounded-pill px-4 py-2 text-sm ${source === key ? 'bg-white text-black' : 'bg-white/10 text-nf-dim hover:text-white'}`}>
                {SOURCE_LABELS[key]}
              </button>
            ))}
          </div>
          <form className="mt-4 flex flex-wrap gap-3" onSubmit={e => { e.preventDefault(); search(1); }}>
            <input value={query} onChange={e => setQuery(e.target.value)} minLength={2}
              placeholder={`Buscar en ${SOURCE_LABELS[source]}`}
              className="min-w-[220px] flex-1 rounded bg-black/40 px-4 py-3 text-base outline-none ring-1 ring-nf-line focus:ring-white/40" />
            <Button variant="primary" disabled={busy || query.trim().length < 2}>{busy ? 'Buscando…' : 'Buscar'}</Button>
          </form>
          {source !== 'leetx' && (
            <label className="mt-3 inline-flex items-center gap-2 text-sm text-nf-dim">
              <input type="checkbox" checked={spanishOnly} onChange={e => setSpanishOnly(e.target.checked)} />
              Solo subtítulos en español
            </label>
          )}
          {notice && <p className="mt-3 text-sm text-nf-dim" role="status">{notice}</p>}
          <div className="mt-4 divide-y divide-white/10">
            {results.map((item, index) => (
              <div key={`${item.torrent_id || item.magnet || item.title}-${index}`} className="flex flex-wrap items-center gap-4 py-4">
                <div className="min-w-0 flex-1">
                  <p className="break-words text-sm font-medium">{item.title}</p>
                  <p className="mt-1 text-xs text-nf-faint">
                    {item.size || 'Tamaño desconocido'} · ↑{item.seeders || 0} ↓{item.leechers || 0}
                    {item.pub_date ? ` · ${item.pub_date}` : ''}
                  </p>
                </div>
                <Button size="sm" variant="light" onClick={() => add(item)}>Añadir a Transmission</Button>
              </div>
            ))}
          </div>
          {results.length > 0 && (
            <div className="mt-4 flex justify-center gap-3">
              <Button size="sm" disabled={page <= 1 || busy} onClick={() => search(page - 1)}>Anterior</Button>
              <span className="self-center text-xs text-nf-dim">Página {page}</span>
              <Button size="sm" disabled={busy} onClick={() => search(page + 1)}>Siguiente</Button>
            </div>
          )}
        </section>

        <section className="mb-20 mt-10">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-xl font-semibold">Transmission</h2>
            <button onClick={loadTorrents} className="text-sm text-nf-dim hover:text-white">Actualizar</button>
          </div>
          {torrents.length === 0 ? <p className="text-sm text-nf-faint">No hay torrents o Transmission no está disponible.</p> :
            <div className="space-y-3">
              {torrents.map(torrent => (
                <article key={torrent.id} className="rounded-panel border border-nf-line bg-nf-surface p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="break-words text-sm font-medium">{torrent.name}</p>
                      <p className="mt-1 text-xs text-nf-faint">
                        {torrent.size_str} · {torrent.status} · {torrent.peers_connected} pares
                        {torrent.eta ? ` · ${torrent.eta}` : ''}{torrent.ratio ? ` · ratio ${torrent.ratio}` : ''}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      {torrent.status === 'stopped' ?
                        <Button size="sm" onClick={() => action(torrent, 'resume')}>Reanudar</Button> :
                        <Button size="sm" onClick={() => action(torrent, 'pause')}>Pausar</Button>}
                      <Button size="sm" variant="danger" onClick={() => action(torrent, 'remove')}>Eliminar</Button>
                    </div>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10">
                    <div className="h-full bg-nf-red transition-[width]" style={{ width: `${Math.max(0, Math.min(100, torrent.progress))}%` }} />
                  </div>
                </article>
              ))}
            </div>}
        </section>
      </div>
    </Shell>
  );
}
