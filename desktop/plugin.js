import {
  PANES_AREA,
  PALETTE_AREA,
  TRANSCRIPT_DIRECTIVE_AREA,
  host,
  atom,
  useValue,
  useQuery,
  Button,
  EmptyState,
  ScrollArea,
  Loader
} from '@hermes/plugin-sdk'
import { useEffect, useState, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const UUID_RE = /^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$/
const PANE_AREA = PANES_AREA || 'panes'

const $activePhotoSet = atom(null)

function validatePhotoAttrs(attrs) {
  if (!attrs || typeof attrs !== 'object') {
    return null
  }
  const { ids, title } = attrs
  if (!ids || typeof ids !== 'string') {
    return null
  }
  const trimmed = ids.trim()
  if (!trimmed) {
    return null
  }
  const list = trimmed
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  if (list.length === 0 || list.length > 200) {
    return null
  }
  for (let i = 0; i < list.length; i++) {
    if (!UUID_RE.test(list[i])) {
      return null
    }
  }
  return {
    ids: list,
    title: typeof title === 'string' ? title.trim() : ''
  }
}

function formatTileDate(dateStr) {
  if (!dateStr || typeof dateStr !== 'string') {
    return ''
  }
  if (dateStr.includes('T')) {
    return dateStr.split('T')[0]
  }
  return dateStr
}

function PhotosDirective({ attrs, ctx }) {
  const validated = validatePhotoAttrs(attrs)
  const ids = validated ? validated.ids : []
  const title = validated ? validated.title : ''
  const idsKey = ids.join(',')

  useEffect(() => {
    if (!validated) return
    const current = $activePhotoSet.get()
    if (!current || current.ids.join(',') !== idsKey) {
      $activePhotoSet.set({ ids, title })
    }
  }, [idsKey, title])

  const inlineIds = ids.slice(0, 12)
  const inlineKey = inlineIds.join(',')
  const small = ids.length <= 3

  const { data, isLoading } = useQuery({
    queryKey: ['lumina', 'thumbs', inlineKey, small ? 'm' : 's'],
    queryFn: () => ctx.rest(`/thumbs?uuids=${inlineKey}&size=${small ? 'm' : 's'}`),
    enabled: ids.length > 0
  })

  if (!validated) {
    return null
  }

  const remainingCount = ids.length - 12
  const hasMore = remainingCount > 0

  const itemsByUuid = new Map()
  if (data && Array.isArray(data.items)) {
    for (const item of data.items) {
      if (item && item.uuid) {
        itemsByUuid.set(item.uuid, item)
      }
    }
  }

  const handleTileClick = (uuid) => {
    $activePhotoSet.set({ ids, title })
    ctx.rest(`/open?uuid=${uuid}`).catch(() => {})
  }

  const handleMoreClick = () => {
    $activePhotoSet.set({ ids, title })
    host.revealPane?.('lumina:pane')
  }

  return jsxs('div', {
    className: 'flex flex-col gap-2 my-2 p-2 rounded-lg',
    style: {
      backgroundColor: 'var(--ui-bg-secondary)',
      border: '1px solid var(--ui-stroke-secondary)'
    },
    children: [
      title
        ? jsx('div', {
            className: 'text-sm font-medium px-1',
            style: { color: 'var(--ui-text-primary)' },
            children: title
          })
        : null,
      isLoading
        ? jsx('div', {
            className: 'flex items-center justify-center p-4',
            children: jsx(Loader, {})
          })
        : jsxs('div', {
            className: 'grid gap-2',
            style: {
              gridTemplateColumns: small
                ? 'repeat(auto-fill, minmax(160px, 1fr))'
                : 'repeat(auto-fill, minmax(72px, 1fr))'
            },
            children: [
              ...inlineIds.map((uuid) => {
                const item = itemsByUuid.get(uuid)
                const dataUrl = item?.data_url
                const dateStr = formatTileDate(item?.date)

                return jsx('button', {
                  key: uuid,
                  type: 'button',
                  onClick: () => handleTileClick(uuid),
                  className:
                    (small ? '' : 'aspect-square ') +
                    'rounded overflow-hidden flex items-center justify-center p-0 transition-opacity hover:opacity-90',
                  style: {
                    backgroundColor: small ? 'var(--ui-bg-secondary)' : 'var(--ui-bg-tertiary)',
                    border: '1px solid var(--ui-stroke-tertiary)',
                    cursor: 'pointer',
                    ...(small ? { aspectRatio: '4 / 3' } : {})
                  },
                  title: item?.title || dateStr || 'Photo',
                  children: dataUrl
                    ? jsx('img', {
                        src: dataUrl,
                        alt: item?.title || dateStr || 'Photo',
                        className: small ? 'w-full h-full object-contain' : 'w-full h-full object-cover'
                      })
                    : jsx('div', {
                        className:
                          'w-full h-full flex items-center justify-center p-1 text-center select-none',
                        style: {
                          backgroundColor: 'var(--ui-bg-tertiary)',
                          color: 'var(--ui-text-secondary)'
                        },
                        children: jsx('span', {
                          className: 'text-[11px] leading-tight',
                          children: dateStr || 'Photo'
                        })
                      })
                })
              }),
              hasMore
                ? jsx(Button, {
                    key: '__more__',
                    variant: 'outline',
                    onClick: handleMoreClick,
                    className:
                      'aspect-square h-auto w-full flex flex-col items-center justify-center p-1 font-semibold text-xs',
                    style: {
                      backgroundColor: 'var(--ui-bg-primary)',
                      borderColor: 'var(--ui-stroke-secondary)',
                      color: 'var(--ui-text-primary)'
                    },
                    children: `+${remainingCount} more`
                  })
                : null
            ].filter(Boolean)
          })
    ].filter(Boolean)
  })
}

function LuminaPane({ ctx }) {
  const activeSet = useValue($activePhotoSet)
  const { data: status } = useQuery({
    queryKey: ['lumina', 'status'],
    queryFn: () => ctx.rest('/status')
  })

  const ids = activeSet?.ids || []
  const idsKey = ids.join(',')

  const { data: thumbsData, isLoading: isThumbsLoading } = useQuery({
    queryKey: ['lumina', 'thumbs', idsKey, 'm'],
    // backend caps /thumbs at 60 ids per request
    queryFn: async () => {
      const items = []
      for (let i = 0; i < ids.length; i += 60) {
        const r = await ctx.rest(`/thumbs?uuids=${ids.slice(i, i + 60).join(',')}&size=m`)
        items.push(...(r?.items || []))
      }
      return { items }
    },
    enabled: ids.length > 0
  })

  const itemsByUuid = new Map()
  if (thumbsData && Array.isArray(thumbsData.items)) {
    for (const item of thumbsData.items) {
      if (item && item.uuid) {
        itemsByUuid.set(item.uuid, item)
      }
    }
  }

  const handleTileClick = (uuid) => {
    ctx.rest(`/open?uuid=${uuid}`).catch(() => {})
  }

  const hasPhotos = ids.length > 0

  return jsx(ScrollArea, {
    className: 'h-full w-full',
    children: jsxs('div', {
      className: 'p-3 flex flex-col gap-3',
      children: [
        hasPhotos
          ? jsx('div', {
              className: 'text-sm font-semibold truncate px-0.5',
              style: { color: 'var(--ui-text-primary)' },
              children: `${activeSet?.title || 'Photos'} \u00b7 ${ids.length}`
            })
          : null,
        status
          ? jsxs('div', {
              className: 'flex flex-col gap-2 p-2.5 rounded',
              style: {
                backgroundColor: 'var(--ui-bg-secondary)',
                border: '1px solid var(--ui-stroke-secondary)'
              },
              children: [
                typeof status.photos === 'number'
                  ? jsx('div', {
                      className: 'text-xs font-medium',
                      style: { color: 'var(--ui-text-primary)' },
                      children: `${status.photos} photos in library`
                    })
                  : null,
                status.access === 'no_access'
                  ? jsxs('div', {
                      className: 'flex flex-col gap-1 mt-1 p-2 rounded text-xs',
                      style: {
                        backgroundColor: 'var(--ui-bg-tertiary)',
                        border: '1px solid var(--ui-stroke-tertiary)',
                        color: 'var(--ui-text-primary)'
                      },
                      children: [
                        jsx('div', {
                          className: 'font-semibold',
                          style: { color: 'var(--ui-text-primary)' },
                          children: 'Full Disk Access Required'
                        }),
                        jsx('div', {
                          className: 'leading-relaxed select-text',
                          style: { color: 'var(--ui-text-secondary)' },
                          children:
                            'System Settings > Privacy & Security > Full Disk Access > enable Hermes'
                        })
                      ]
                    })
                  : status.access === 'error'
                    ? jsx('div', {
                        className: 'text-xs mt-1',
                        style: { color: 'var(--ui-text-secondary)' },
                        children: status.message || 'Lumina cannot read the Photos library.'
                      })
                    : null
              ].filter(Boolean)
            })
          : null,
        hasPhotos
          ? jsxs('div', {
              className: 'flex flex-col gap-2',
              children: [
                isThumbsLoading
                  ? jsx('div', {
                      className: 'flex items-center justify-center p-6',
                      children: jsx(Loader, {})
                    })
                  : jsx('div', {
                      className: 'grid grid-cols-2 gap-2',
                      children: ids.map((uuid) => {
                        const item = itemsByUuid.get(uuid)
                        const dataUrl = item?.data_url
                        const dateStr = formatTileDate(item?.date)

                        return jsx('button', {
                          key: uuid,
                          type: 'button',
                          onClick: () => handleTileClick(uuid),
                          className:
                            'aspect-square rounded overflow-hidden flex items-center justify-center p-0 transition-opacity hover:opacity-90',
                          style: {
                            backgroundColor: 'var(--ui-bg-tertiary)',
                            border: '1px solid var(--ui-stroke-tertiary)',
                            cursor: 'pointer'
                          },
                          title: item?.title || dateStr || 'Photo',
                          children: dataUrl
                            ? jsx('img', {
                                src: dataUrl,
                                alt: item?.title || dateStr || 'Photo',
                                className: 'w-full h-full object-cover'
                              })
                            : jsx('div', {
                                className:
                                  'w-full h-full flex items-center justify-center p-2 text-center select-none',
                                style: {
                                  backgroundColor: 'var(--ui-bg-tertiary)',
                                  color: 'var(--ui-text-secondary)'
                                },
                                children: jsx('span', {
                                  className: 'text-xs leading-tight',
                                  children: dateStr || 'Photo'
                                })
                              })
                        })
                      })
                    })
              ]
            })
          : jsx(EmptyState, {
              title: 'No photos selected',
              description: 'Open a photo set from chat to view medium thumbnails here.'
            })
      ].filter(Boolean)
    })
  })
}

const REDACT_ID_RE = /^[0-9a-f]{12}$/
const PRESET_LABELS = { hotel: 'Hotel-safe', airline: 'Airline-safe', all: 'Everything hidden' }
const KIND_LABELS = { number: 'ID number', dob: 'Date of birth', mrz: 'Machine code', name: 'Name', contact: 'Contact', password: 'Password' }

function RedactCard({ data }) {
  const [pos, setPos] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 50 : 100
  )
  const [isDragging, setIsDragging] = useState(false)
  const boxRef = useRef(null)
  const animatingRef = useRef(true)

  useEffect(() => {
    if (typeof window === 'undefined' || window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) return
    let start = null, rafId
    const step = (now) => {
      if (!animatingRef.current) return
      if (!start) start = now
      const p = Math.min((now - start) / 900, 1)
      setPos(Math.round(100 - 50 * (1 - (1 - p) * (1 - p))))
      if (p < 1) rafId = requestAnimationFrame(step)
    }
    rafId = requestAnimationFrame(step)
    return () => { animatingRef.current = false; if (rafId) cancelAnimationFrame(rafId) }
  }, [])

  const updatePos = (clientX) => {
    const rect = boxRef.current?.getBoundingClientRect()
    if (rect && rect.width > 0) setPos(Math.round((Math.max(0, Math.min(rect.width, clientX - rect.left)) / rect.width) * 100))
  }
  const onPointerDown = (e) => {
    animatingRef.current = false
    e.currentTarget.setPointerCapture(e.pointerId)
    setIsDragging(true)
    updatePos(e.clientX)
  }

  return jsxs('div', {
    className: 'flex flex-col gap-2.5 my-2 p-3 rounded-lg select-none',
    style: { maxWidth: '560px', backgroundColor: 'var(--ui-bg-secondary)', border: '1px solid var(--ui-stroke-secondary)' },
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 text-xs',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-1.5 font-semibold shrink-0',
            style: { color: 'var(--ui-text-primary)' },
            children: [jsx('span', { children: '\uD83D\uDEE1\uFE0F' }), jsx('span', { children: 'Lumina Guardian' })]
          }),
          data.document ? jsx('span', { className: 'truncate font-medium', style: { color: 'var(--ui-text-secondary)' }, children: `\u00b7 ${data.document}` }) : null,
          jsx('span', {
            className: 'ml-auto text-[11px] px-2 py-0.5 rounded-full shrink-0 font-medium',
            style: { backgroundColor: 'var(--ui-bg-tertiary)', border: '1px solid var(--ui-stroke-tertiary)', color: 'var(--ui-text-secondary)' },
            children: PRESET_LABELS[data.preset] || data.preset || 'Protected'
          })
        ].filter(Boolean)
      }),
      jsxs('div', {
        ref: boxRef,
        role: 'slider',
        tabIndex: 0,
        'aria-label': 'Redaction comparison',
        'aria-valuenow': Math.round(pos),
        'aria-valuemin': 0,
        'aria-valuemax': 100,
        onPointerDown,
        onPointerMove: (e) => { if (isDragging || e.buttons === 1) updatePos(e.clientX) },
        onPointerUp: () => setIsDragging(false),
        onPointerCancel: () => setIsDragging(false),
        onKeyDown: (e) => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault()
            animatingRef.current = false
            setPos((p) => Math.max(0, Math.min(100, p + (e.key === 'ArrowRight' ? 5 : -5))))
          }
        },
        className: 'relative w-full overflow-hidden rounded outline-none focus-visible:ring-1',
        style: { backgroundColor: 'var(--ui-bg-tertiary)', border: '1px solid var(--ui-stroke-tertiary)', cursor: 'ew-resize', touchAction: 'none' },
        children: [
          jsx('img', { src: data.before, alt: 'Original', className: 'absolute top-0 left-0 w-full h-auto pointer-events-none select-none' }),
          jsx('img', { src: data.after, alt: 'Safe copy', className: 'relative block w-full h-auto pointer-events-none select-none', style: { clipPath: `inset(0 0 0 ${pos}%)` } }),
          jsx('span', { className: 'absolute top-2 left-2 text-[10px] font-semibold px-1.5 py-0.5 rounded pointer-events-none select-none', style: { backgroundColor: 'var(--ui-bg-primary)', color: 'var(--ui-text-primary)', border: '1px solid var(--ui-stroke-secondary)' }, children: 'Original' }),
          jsx('span', { className: 'absolute top-2 right-2 text-[10px] font-semibold px-1.5 py-0.5 rounded pointer-events-none select-none', style: { backgroundColor: 'var(--ui-bg-primary)', color: 'var(--ui-text-primary)', border: '1px solid var(--ui-stroke-secondary)' }, children: 'Safe copy' }),
          jsxs('div', {
            className: 'absolute top-0 bottom-0 pointer-events-none',
            style: { left: `${pos}%`, width: '2px', transform: 'translateX(-50%)', backgroundColor: '#fff', boxShadow: '0 0 2px rgba(0,0,0,0.5)' },
            children: [
              jsx('div', {
                className: 'absolute top-1/2 -translate-y-1/2 -translate-x-1/2 rounded-full flex items-center justify-center shadow',
                style: { left: '50%', width: '22px', height: '22px', backgroundColor: 'var(--ui-bg-primary)', border: '1px solid var(--ui-stroke-secondary)', color: 'var(--ui-text-primary)' },
                children: jsx('span', { className: 'text-[11px] leading-none', children: '\u2194' })
              })
            ]
          })
        ]
      }),
      data.verified && data.remaining === 0
        ? jsxs('div', {
            className: 'flex flex-wrap items-center gap-1.5 text-xs',
            style: { color: 'var(--ui-text-primary)' },
            children: [
              jsx('span', { className: 'font-bold', children: '\u2713' }),
              jsx('span', { children: `Re-read on-device: ${data.found} hidden, 0 remain` }),
              Array.isArray(data.kinds) && data.kinds.length > 0
                ? jsx('div', {
                    className: 'flex flex-wrap items-center gap-1 ml-auto',
                    children: data.kinds.map((k) =>
                      jsx('span', { key: k, className: 'text-[10px] px-1.5 py-0.5 rounded font-medium', style: { backgroundColor: 'var(--ui-bg-tertiary)', border: '1px solid var(--ui-stroke-tertiary)', color: 'var(--ui-text-secondary)' }, children: KIND_LABELS[k] || k })
                    )
                  })
                : null
            ].filter(Boolean)
          })
        : jsx('div', { className: 'flex items-center gap-1.5 text-xs font-medium', style: { color: 'var(--ui-text-primary)' }, children: '\u26A0 Not verified \u2014 do not send' })
    ]
  })
}

function RedactDirective({ attrs, ctx }) {
  const rawId = attrs && typeof attrs === 'object' ? attrs.id : null
  const id = typeof rawId === 'string' && REDACT_ID_RE.test(rawId.trim()) ? rawId.trim() : null

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['lumina', 'redact', id],
    queryFn: () => ctx.rest('/redact?id=' + id),
    enabled: Boolean(id)
  })

  if (!id) return null

  if (isLoading) {
    return jsx('div', {
      className: 'w-full rounded-lg animate-pulse my-2',
      style: { maxWidth: '560px', aspectRatio: '16 / 10', backgroundColor: 'var(--ui-bg-secondary)', border: '1px solid var(--ui-stroke-secondary)' }
    })
  }

  if (isError || !data || data.status === 404 || /404/.test(String(error?.message || error?.status || ''))) {
    return jsx('div', {
      className: 'text-xs my-2 px-1',
      style: { color: 'var(--ui-text-secondary)' },
      children: 'This safe copy has expired.'
    })
  }

  return jsx(RedactCard, { data })
}

export default {
  id: 'lumina',
  name: 'Lumina',
  register(ctx) {
    ctx.register({
      id: 'photos',
      area: TRANSCRIPT_DIRECTIVE_AREA,
      data: {
        name: 'lumina-photos',
        render: (props) => jsx(PhotosDirective, { ...props, ctx })
      }
    })

    ctx.register({
      id: 'redact',
      area: TRANSCRIPT_DIRECTIVE_AREA,
      data: {
        name: 'lumina-redact',
        render: (props) => jsx(RedactDirective, { ...props, ctx })
      }
    })

    ctx.register({
      id: 'pane',
      area: PANE_AREA,
      title: 'Lumina',
      data: {
        placement: 'right',
        width: '340px'
      },
      render: () => jsx(LuminaPane, { ctx })
    })

    ctx.register({
      id: 'open-pane',
      area: PALETTE_AREA,
      title: 'Lumina: Open photo pane',
      data: {
        id: 'lumina.open-pane',
        label: 'Lumina: Open photo pane',
        keywords: ['lumina', 'photos', 'open', 'pane'],
        run: () => {
          host.revealPane?.('lumina:pane')
        }
      }
    })
  }
}
