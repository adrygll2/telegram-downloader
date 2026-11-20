import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch, streamUrl } from '../services/api';
import { fetchMediaFiles } from '../services/media';
import { cachedContinueWatching, continueWatching, type Watched } from '../tv/progress';
import { toast } from '../tv/toast';
import Screen from '../components/Screen';
import Row from '../components/Row';
import PosterCard from '../components/PosterCard';
import TitleDetail, { type DetailInput } from '../components/TitleDetail';
import Player from '../components/Player';
import Dialog, { type DialogAction } from '../components/Dialog';
import type { BrowseItem, BrowseRow, BrowseSeccion, BrowseTop, Featured, Peticion } from '../types';

interface Props {
  filter?: 'movie' | 'series';
  heading: string;
  /** Plataformas: una fila por Netflix, HBO Max... (`/browse/plataformas`)
   *  en vez de la portada. Sin "Continuar viendo", que es cosa del inicio. */
  fuente?: 'portada' | 'plataformas';
}

const ROWS_AROUND = 2;
/** Cuantas tarjetas antes del final se pide la pagina siguiente: con el mando
 *  se avanza de una en una, y asi la siguiente llega antes de que se note. */
const PEDIR_ANTES = 6;
const POR_PAGINA = 20;

/** De dónde salen los datos de un top, bajo su título: sin esto un «Top 10»
 *  de TMDB se leería como si fuera lo más visto de verdad. */
const NOTA_TOP: Record<BrowseTop['fuente'], string> = {
  netflix: 'Datos oficiales de Netflix para España (semanales)',
  tmdb: 'Según las tendencias de la semana en TMDB',
};

/** Pedida y esperando: en la cola o ya escrita en Telegram. */
function estaPedida(estado: string | undefined): boolean {
  return estado === 'en_cola' || estado === 'enviada';
}

/** La etiqueta de una tarjeta de top que no está en el catálogo. */
function avisoDe(item: BrowseItem): string | undefined {
  if (item.en_catalogo !== false) return undefined;
  if (item.sin_ficha) return 'Sin ficha';
  return estaPedida(item.peticion?.estado) ? 'Pedida' : 'No la tenemos';
}

function toFeatured(item: BrowseItem, rowKey: string): Featured {
  // El subtítulo del top ("Temporada 2") y, si no lo tenemos, que no lo
  // tenemos: el panel de arriba es lo que se lee, la tarjeta solo se ve.
  const extra = [item.subtitulo, avisoDe(item)].filter(Boolean).join(' · ');
  return {
    // Lo que no está en el catálogo puede venir sin id propio.
    key: `${rowKey}-${item.id ?? `${item.media_type}-${item.tmdb_id}`}`,
    title: item.title,
    kind: item.media_type,
    poster: item.poster,
    backdrop: item.backdrop,
    year: item.year,
    rating: item.rating,
    overview: item.overview,
    genres: item.genres,
    subtitle: extra ? extra
      : item.plataforma ? `En ${item.plataforma}`
      : item.media_type === 'series' && item.episode_count ? `${item.episode_count} episodios` : undefined,
  };
}

/** Logo y nombre de la plataforma, encima de su primera fila. */
function CabeceraSeccion({ seccion, primera }: { seccion: BrowseSeccion; primera: boolean }) {
  const [roto, setRoto] = useState(false);
  return (
    <div className={`flex items-center space-x-4 mb-3${primera ? '' : ' pt-4'}`}>
      {seccion.logo && !roto ? (
        <img src={seccion.logo} alt="" className="w-14 h-14 rounded-xl object-cover bg-white/10 shrink-0"
          decoding="async" onError={() => setRoto(true)} />
      ) : (
        <span className="w-14 h-14 rounded-xl bg-white/15 shrink-0 flex items-center justify-center text-lead font-bold">
          {seccion.nombre.charAt(0)}
        </span>
      )}
      <span className="text-h1 font-bold">{seccion.nombre}</span>
    </div>
  );
}

/** El cuadro de una tarjeta de top que no tenemos: qué se puede hacer con ella. */
function cuadroPedir(item: BrowseItem, pedir: () => void, cerrar: () => void): { text: string; actions: DialogAction[] } {
  const datos = [item.year ? String(item.year) : '', item.media_type === 'series' ? 'Serie' : 'Película'].filter(Boolean).join(' · ');
  const p = item.peticion;
  if (estaPedida(p?.estado) && p?.mia) {
    return { text: `${datos}. Ya la has pedido: te avisaremos cuando esté.`, actions: [{ label: 'Cerrar', onSelect: cerrar, primary: true }] };
  }
  if (estaPedida(p?.estado)) {
    // La ha pedido otra cuenta: pedirla otra vez no manda nada nuevo a
    // Telegram, solo apunta a esta cuenta en la lista de avisos.
    return {
      text: `${datos}. Ya la ha pedido otra persona. Si quieres, te avisamos a ti también cuando esté.`,
      actions: [{ label: 'Avisarme', onSelect: pedir, primary: true }, { label: 'Cancelar', onSelect: cerrar }],
    };
  }
  const motivo = p?.estado === 'fallida'
    ? 'La última vez que se pidió no se encontró; puedes volver a intentarlo.'
    : 'No está en el catálogo. Si la pides, la buscaremos y te avisaremos cuando esté.';
  return { text: `${datos}. ${motivo}`, actions: [{ label: 'Pedir', onSelect: pedir, primary: true }, { label: 'Cancelar', onSelect: cerrar }] };
}

interface FilaPintada {
  row: BrowseRow;
  items: { item: BrowseItem; f: Featured }[];
}

function watchedToFeatured(w: Watched): Featured {
  // La tarjeta del episodio siguiente no lleva porcentaje: no se ha visto
  // nada de el todavia, el 0 % solo seria ruido.
  const visto = w.duration > 0 ? Math.round((w.position / w.duration) * 100) : 0;
  const subtitulo = w.next_episode
    ? `Empezar ${w.subtitle || 'el siguiente'}`
    : w.subtitle ? `${w.subtitle} · ${visto} %` : `${visto} % visto`;
  return {
    key: `cw-${w.path}`,
    title: w.title,
    kind: 'file',
    poster: w.poster,
    backdrop: w.backdrop,
    subtitle: subtitulo,
    progress: w.duration > 0 ? w.position / w.duration : 0,
  };
}

/**
 * Portada, Peliculas, Series y Plataformas: carriles sobre /browse/home (o
 * /browse/plataformas). Solo montan tarjetas las filas cercanas a la
 * enfocada; el resto deja su hueco.
 */
export default function Catalog({ filter, heading, fuente = 'portada' }: Props) {
  const portada = !filter && fuente === 'portada';
  const [rows, setRows] = useState<BrowseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [focusRow, setFocusRow] = useState(0);
  const [detail, setDetail] = useState<DetailInput | null>(null);
  const [playing, setPlaying] = useState<Watched | null>(null);
  const [pidiendo, setPidiendo] = useState<BrowseItem | null>(null);
  const enVuelo = useRef(false);
  // La semilla de /browse/home: con ella /browse/row sigue el mismo orden y
  // la pagina 2 continua a la 1 en vez de repetir titulos.
  const semilla = useRef('');
  const paginando = useRef(new Set<string>());
  // De la cache primero, para que la fila este ahi nada mas abrir, y acto
  // seguido lo que diga el servidor (que sabe lo visto en el movil o la web).
  const [resume, setResume] = useState<Watched[]>(() => (portada ? cachedContinueWatching() : []));

  useEffect(() => {
    if (!portada) return;
    continueWatching().then(setResume).catch(() => {});
  }, [portada]);

  useEffect(() => {
    (async () => {
      try {
        // El tipo lo filtra el servidor (como en la web y el movil): filtrado
        // aqui, las paginas siguientes de un carril traerian del otro tipo.
        const q = filter ? `?media_type=${filter}` : '';
        const res = await apiFetch(`${fuente === 'plataformas' ? '/browse/plataformas' : '/browse/home'}${q}`);
        const data = await res.json();
        semilla.current = data.seed || '';
        let list: BrowseRow[] = data.rows || [];
        if (filter) {
          list = list.map((r) => ({ ...r, items: r.items.filter((i) => i.media_type === filter) })).filter((r) => r.items.length > 0);
        }
        setRows(list);
      } catch {
        toast('No se ha podido cargar el catálogo', 'error', 5000);
      } finally {
        setLoading(false);
      }
    })();
  }, [filter, fuente]);

  // Por fila y por identidad: al marcar un título como pedido solo cambian
  // las filas que lo llevan, y las tarjetas del resto (memo por `item`) no se
  // vuelven a pintar.
  const pintadas = useRef(new WeakMap<BrowseRow, FilaPintada>());
  // Y por tarjeta: al llegar otra pagina la fila es nueva, pero las tarjetas
  // que ya tenia conservan su `Featured` y no se repintan.
  const tarjetas = useRef(new WeakMap<BrowseItem, Featured>());
  const featuredRows = useMemo(() => rows.map((r) => {
    let fila = pintadas.current.get(r);
    if (!fila) {
      fila = { row: r, items: r.items.map((it) => {
        let f = tarjetas.current.get(it);
        if (!f) {
          f = toFeatured(it, r.genre);
          tarjetas.current.set(it, f);
        }
        return { item: it, f };
      }) };
      pintadas.current.set(r, fila);
    }
    return fila;
  }), [rows]);

  /**
   * La pagina siguiente de un carril, cuando el foco se acerca a su final. Los
   * carriles llegaban con la primera pagina (20 titulos) y ahi se acababan:
   * la tele nunca pedia `/browse/row`, que es lo que hace infinitos los de la
   * web y el movil.
   */
  const cargarMas = useCallback(async (row: BrowseRow) => {
    if (!row.key || paginando.current.has(row.genre)) return;
    if (row.total !== undefined && row.items.length >= row.total) return;
    paginando.current.add(row.genre);
    try {
      const q = new URLSearchParams({ key: row.key, seed: semilla.current,
        offset: String(row.items.length), limit: String(POR_PAGINA) });
      if (filter) q.set('media_type', filter);
      const data = await (await apiFetch(`/browse/row?${q}`)).json();
      const nuevos: BrowseItem[] = data.items || [];
      setRows((prev) => prev.map((r) => {
        if (r.genre !== row.genre) return r;
        const ya = new Set(r.items.map((i) => `${i.media_type}-${i.tmdb_id}`));
        const items = r.items.concat(nuevos.filter((i) => !ya.has(`${i.media_type}-${i.tmdb_id}`)));
        // Una pagina vacia (o solo con repetidos) es el final aunque el total
        // diga otra cosa: sin esto se volveria a pedir en cada pulsacion.
        const total = items.length === r.items.length ? items.length : data.total ?? r.total;
        return { ...r, items, total };
      }));
    } catch {
      // Sin red el carril se queda como esta; se reintenta en la siguiente pulsacion.
    } finally {
      paginando.current.delete(row.genre);
    }
  }, [filter]);

  /** Apunta en local que esta cuenta lo ha pedido, sin volver a pedir la portada. */
  const marcarPedida = useCallback((item: BrowseItem, estado: Peticion['estado']) => {
    const es = (i: BrowseItem) => i.tmdb_id === item.tmdb_id && i.media_type === item.media_type && i.en_catalogo === false;
    setRows((prev) => prev.map((r) => (r.items.some(es)
      ? { ...r, items: r.items.map((i) => (es(i) ? { ...i, peticion: { estado, mia: true } } : i)) }
      : r)));
  }, []);

  const pedir = useCallback(async (item: BrowseItem) => {
    setPidiendo(null);
    if (enVuelo.current) return;
    enVuelo.current = true;
    try {
      const res = await apiFetch('/peticiones', {
        method: 'POST',
        body: JSON.stringify({ tmdb_id: item.tmdb_id, media_type: item.media_type }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof data.detail === 'string' ? data.detail : 'No se ha podido pedir', 'error', 5000);
        return;
      }
      // Pedir ya no escribe en Telegram: entra en una cola que sale de una en
      // una cada hora, y el puesto es lo que dice cuánto falta.
      toast(data.ya_pedida ? 'Ya estaba pedida: te avisaremos también a ti'
        : data.puesto ? `En cola (puesto ${data.puesto}): te avisaremos cuando esté`
        : 'Pedida: te avisaremos cuando esté', 'ok', 5000);
      marcarPedida(item, data.estado || 'enviada');
    } catch {
      toast('No se ha podido pedir: el servidor no contesta', 'error', 5000);
    } finally {
      enVuelo.current = false;
    }
  }, [marcarPedida]);

  const openItem = useCallback(async (item: BrowseItem, f: Featured) => {
    try {
      const { results } = await fetchMediaFiles(item.tmdb_id, item.media_type);
      setDetail({ kind: item.media_type, tmdbId: item.tmdb_id, meta: f, files: results });
    } catch {
      setDetail({ kind: item.media_type, tmdbId: item.tmdb_id, meta: f, files: [] });
    }
  }, []);

  const onPlayerClose = useCallback(() => {
    setPlaying(null);
    if (portada) continueWatching().then(setResume).catch(() => {});
  }, [portada]);

  const hasResume = resume.length > 0;
  const rowOffset = hasResume ? 1 : 0;
  const isMounted = (i: number) => Math.abs(i - focusRow) <= ROWS_AROUND;
  // El destacado de partida sale de lo ultimo que ha llegado a alguna
  // plataforma; luego el panel sigue a la tarjeta enfocada, como siempre.
  const fallback = useMemo(() => {
    const i = rows.findIndex((r) => r.key === 'plataforma:todas');
    const fila = i >= 0 ? featuredRows[i] : featuredRows[0];
    return fila?.items.find(({ f }) => f.backdrop)?.f || fila?.items[0]?.f || null;
  }, [rows, featuredRows]);

  return (
    <Screen hero heading={heading} heroFallback={fallback} ready={!loading && (rows.length > 0 || hasResume)} onRowFocus={setFocusRow}>
      {hasResume && (
        <Row index={0} title="Continuar viendo">
          {resume.map((w, i) => (
            <PosterCard key={w.path} index={i} item={watchedToFeatured(w)} autoFocus={i === 0} onSelect={() => setPlaying(w)} />
          ))}
        </Row>
      )}

      {featuredRows.map(({ row, items }, ri) => {
        // La cabecera va dentro de la primera fila de cada plataforma (y no
        // como un hijo más de la columna): así cada hijo sigue siendo una fila
        // enfocable con su índice, y al enfocarla Screen sube la fila entera,
        // con su logo encima.
        const seccion = row.seccion && row.seccion.clave !== featuredRows[ri - 1]?.row.seccion?.clave ? row.seccion : null;
        return (
          <Row key={row.genre} index={ri + rowOffset} title={row.titulo ?? row.genre}
            note={row.top ? NOTA_TOP[row.top.fuente] : undefined}
            cabecera={seccion ? <CabeceraSeccion seccion={seccion} primera={ri === 0} /> : undefined}
            onFocusIndex={(i) => { if (i >= items.length - PEDIR_ANTES) cargarMas(row); }}>
            {isMounted(ri + rowOffset)
              ? items.map(({ item, f }, i) => (
                  <PosterCard key={f.key} index={i} item={f} autoFocus={ri === 0 && i === 0 && !hasResume}
                    puesto={row.top ? item.puesto ?? i + 1 : undefined} aviso={avisoDe(item)}
                    onSelect={() => {
                      // Un puesto que TMDB no conoce no tiene ficha ni se puede pedir.
                      if (item.sin_ficha) { toast('TMDB no conoce este título: no hay ficha ni se puede pedir'); return; }
                      if (item.en_catalogo === false) setPidiendo(item); else openItem(item, f);
                    }} />
                ))
              : null}
          </Row>
        );
      })}

      {loading && (
        <p className="text-body text-tv-text3" style={{ paddingLeft: 'var(--content-x)' }}>Cargando catálogo…</p>
      )}
      {!loading && rows.length === 0 && fuente === 'plataformas' && (
        <div style={{ paddingLeft: 'var(--content-x)' }}>
          <p className="text-lead text-tv-text2">Todavía no hay nada aquí.</p>
          <p className="text-body text-tv-text3 mt-2">El servidor está mirando en qué plataforma está cada título; vuelve en unos minutos.</p>
        </div>
      )}
      {!loading && rows.length === 0 && fuente === 'portada' && (
        <div style={{ paddingLeft: 'var(--content-x)' }}>
          <p className="text-lead text-tv-text2">Todavía no hay nada indexado.</p>
          <p className="text-body text-tv-text3 mt-2">Añade canales desde la versión web y espera a que termine el escaneo.</p>
        </div>
      )}

      {pidiendo && (() => {
        const cerrar = () => setPidiendo(null);
        const { text, actions } = cuadroPedir(pidiendo, () => pedir(pidiendo), cerrar);
        return <Dialog title={pidiendo.title} text={text} actions={actions} onClose={cerrar} />;
      })()}
      {detail && <TitleDetail input={detail} onClose={() => setDetail(null)} />}
      {playing && (
        <Player src={streamUrl(playing.path)} path={playing.path} title={playing.title} subtitle={playing.subtitle}
          poster={playing.poster} backdrop={playing.backdrop}
          tmdbId={playing.tmdb_id ?? undefined} mediaType={playing.media_type}
          onClose={onPlayerClose} />
      )}
    </Screen>
  );
}
