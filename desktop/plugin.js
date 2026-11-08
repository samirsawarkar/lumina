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
import { useEffect } from 'react'
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
  if (!validated) {
    return null
  }

  const { ids, title } = validated
  const idsKey = ids.join(',')

  useEffect(() => {
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
                    ...(small ? { height: '220px' } : {})
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
