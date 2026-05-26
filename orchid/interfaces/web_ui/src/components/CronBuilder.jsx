/**
 * CronBuilder — human-friendly schedule builder that outputs a cron expression.
 *
 * Supports: every minute, every N minutes, hourly, daily, weekly, monthly.
 * Time/timezone inputs are converted to UTC (what APScheduler uses).
 */
import { useState, useMemo } from 'react'

// ── constants ─────────────────────────────────────────────────────────────────

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Anchorage',
  'Pacific/Honolulu',
  'America/Sao_Paulo',
  'America/Argentina/Buenos_Aires',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Helsinki',
  'Europe/Moscow',
  'Africa/Cairo',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Bangkok',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Pacific/Auckland',
]

// ── tz math ───────────────────────────────────────────────────────────────────

/**
 * Returns hours to ADD to local wall-clock time to get UTC.
 * e.g. America/New_York in winter → +5  (ET is UTC-5)
 *      Asia/Kolkata             → -5.5 (IST is UTC+5:30)
 */
function getOffsetHours(timezone) {
  try {
    const now = new Date()
    const fmtParts = (tz) =>
      new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        year: 'numeric', month: 'numeric', day: 'numeric',
        hour: 'numeric', minute: 'numeric', second: 'numeric',
        hour12: false,
      }).formatToParts(now)

    const get = (parts, type) => {
      const val = parseInt(parts.find(p => p.type === type)?.value ?? '0')
      // Some impls return 24 for midnight with hour12:false
      return type === 'hour' && val === 24 ? 0 : val
    }

    const tzParts = fmtParts(timezone)
    // Build a "fake UTC" date from the TZ wall-clock components so we can diff
    const tzAsUTC = Date.UTC(
      get(tzParts, 'year'), get(tzParts, 'month') - 1, get(tzParts, 'day'),
      get(tzParts, 'hour'), get(tzParts, 'minute'), get(tzParts, 'second'),
    )
    return (now.getTime() - tzAsUTC) / 3600000
  } catch {
    return 0
  }
}

/**
 * Convert a local wall-clock hour+minute in `timezone` to UTC.
 * Returns { utcHour, utcMinute, dayDelta } where dayDelta is -1, 0, or +1.
 */
function localToUTC(localHour, localMinute, timezone) {
  const offsetHours = getOffsetHours(timezone)
  let totalMin = localHour * 60 + localMinute + Math.round(offsetHours * 60)
  let dayDelta = 0
  if (totalMin < 0)    { totalMin += 1440; dayDelta = -1 }
  if (totalMin >= 1440) { totalMin -= 1440; dayDelta = +1 }
  return {
    utcHour:   Math.floor(totalMin / 60),
    utcMinute: totalMin % 60,
    dayDelta,
  }
}

// ── formatting helpers ────────────────────────────────────────────────────────

function pad2(n) { return String(n).padStart(2, '0') }

function fmt12(h, m) {
  const ampm = h < 12 ? 'AM' : 'PM'
  const h12  = h % 12 === 0 ? 12 : h % 12
  return `${h12}:${pad2(m)} ${ampm}`
}

function tzShortName(tz) {
  if (tz === 'UTC') return 'UTC'
  // e.g. "America/New_York" → "New York"
  return tz.split('/').pop().replace(/_/g, ' ')
}

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

// ── cron builder ──────────────────────────────────────────────────────────────

function buildCron(freq, { hour, minute, timezone, days, dayOfMonth, every }) {
  switch (freq) {
    case 'every_minute':   return '* * * * *'
    case 'every_n_minutes': return `*/${every} * * * *`
    case 'hourly':         return `${minute} * * * *`
    case 'daily': {
      const { utcHour, utcMinute } = localToUTC(hour, minute, timezone)
      return `${utcMinute} ${utcHour} * * *`
    }
    case 'weekly': {
      if (!days.length) return ''
      const { utcHour, utcMinute, dayDelta } = localToUTC(hour, minute, timezone)
      const utcDays = [...new Set(days.map(d => (d + dayDelta + 7) % 7))].sort()
      return `${utcMinute} ${utcHour} * * ${utcDays.join(',')}`
    }
    case 'monthly': {
      const { utcHour, utcMinute, dayDelta } = localToUTC(hour, minute, timezone)
      // Clamp day shift to safe range (1–28)
      const dom = Math.max(1, Math.min(28, dayOfMonth + dayDelta))
      return `${utcMinute} ${utcHour} ${dom} * *`
    }
    default: return ''
  }
}

function describe(freq, opts) {
  const { hour, minute, timezone, days, dayOfMonth, every } = opts
  switch (freq) {
    case 'every_minute':    return 'Every minute'
    case 'every_n_minutes': return `Every ${every} minutes`
    case 'hourly':          return `Every hour at :${pad2(minute)}`
    case 'daily': {
      const { utcHour, utcMinute } = localToUTC(hour, minute, timezone)
      const localStr = fmt12(hour, minute)
      const utcStr   = `${pad2(utcHour)}:${pad2(utcMinute)} UTC`
      return timezone === 'UTC'
        ? `Every day at ${utcStr}`
        : `Every day at ${localStr} ${tzShortName(timezone)} · ${utcStr}`
    }
    case 'weekly': {
      if (!days.length) return 'Select at least one day'
      const { utcHour, utcMinute, dayDelta } = localToUTC(hour, minute, timezone)
      const localStr   = fmt12(hour, minute)
      const utcStr     = `${pad2(utcHour)}:${pad2(utcMinute)} UTC`
      const localDays  = days.map(d => DAYS[d]).join(', ')
      const utcDayNums = [...new Set(days.map(d => (d + dayDelta + 7) % 7))].sort()
      const utcDays    = utcDayNums.map(d => DAYS[d]).join(', ')
      const dayPart    = dayDelta !== 0
        ? `${localDays} (UTC: ${utcDays})`
        : localDays
      return timezone === 'UTC'
        ? `Every ${localDays} at ${utcStr}`
        : `Every ${dayPart} at ${localStr} ${tzShortName(timezone)} · ${utcStr}`
    }
    case 'monthly': {
      const { utcHour, utcMinute, dayDelta } = localToUTC(hour, minute, timezone)
      const localStr = fmt12(hour, minute)
      const utcStr   = `${pad2(utcHour)}:${pad2(utcMinute)} UTC`
      const dom      = Math.max(1, Math.min(28, dayOfMonth + dayDelta))
      const domPart  = dayDelta !== 0
        ? `${ordinal(dayOfMonth)} (UTC: ${ordinal(dom)})`
        : ordinal(dayOfMonth)
      return timezone === 'UTC'
        ? `Monthly on the ${domPart} at ${utcStr}`
        : `Monthly on the ${domPart} at ${localStr} ${tzShortName(timezone)} · ${utcStr}`
    }
    default: return ''
  }
}

// ── component ─────────────────────────────────────────────────────────────────

export default function CronBuilder({ onApply, onClose }) {
  const browserTZ = Intl.DateTimeFormat().resolvedOptions().timeZone
  // Put detected timezone first if not already in list
  const tzList = TIMEZONES.includes(browserTZ)
    ? TIMEZONES
    : [browserTZ, ...TIMEZONES]

  const [freq,       setFreq]       = useState('daily')
  const [hour,       setHour]       = useState(9)
  const [minute,     setMinute]     = useState(0)
  const [timezone,   setTimezone]   = useState(TIMEZONES.includes(browserTZ) ? browserTZ : 'UTC')
  const [days,       setDays]       = useState([1])   // Mon
  const [dayOfMonth, setDayOfMonth] = useState(1)
  const [every,      setEvery]      = useState(15)

  const opts = { hour, minute, timezone, days, dayOfMonth, every }
  const cron        = useMemo(() => buildCron(freq, opts), [freq, hour, minute, timezone, days, dayOfMonth, every])
  const description = useMemo(() => describe(freq, opts),  [freq, hour, minute, timezone, days, dayOfMonth, every])

  const toggleDay = (d) =>
    setDays(prev =>
      prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d].sort((a, b) => a - b)
    )

  const needsTime = ['daily', 'weekly', 'monthly'].includes(freq)

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: '#000b',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1100,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 24,
        width: 420,
        maxWidth: '95vw',
        maxHeight: '90vh',
        overflowY: 'auto',
      }}>
        <h4 style={{ marginBottom: 20 }}>🗓 Schedule Builder</h4>

        {/* Frequency */}
        <div className="form-group">
          <label>Frequency</label>
          <select value={freq} onChange={e => setFreq(e.target.value)}>
            <option value="every_minute">Every minute</option>
            <option value="every_n_minutes">Every N minutes</option>
            <option value="hourly">Hourly</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </select>
        </div>

        {/* Every N minutes */}
        {freq === 'every_n_minutes' && (
          <div className="form-group">
            <label>Interval</label>
            <select value={every} onChange={e => setEvery(Number(e.target.value))}>
              {[1, 2, 5, 10, 15, 20, 30].map(n => (
                <option key={n} value={n}>{n} minute{n > 1 ? 's' : ''}</option>
              ))}
            </select>
          </div>
        )}

        {/* Hourly — pick minute */}
        {freq === 'hourly' && (
          <div className="form-group">
            <label>At minute</label>
            <select value={minute} onChange={e => setMinute(Number(e.target.value))}>
              {[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].map(m => (
                <option key={m} value={m}>:{pad2(m)}</option>
              ))}
            </select>
          </div>
        )}

        {/* Weekly — day picker */}
        {freq === 'weekly' && (
          <div className="form-group">
            <label>Days</label>
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
              {DAYS.map((name, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => toggleDay(idx)}
                  style={{
                    padding: '5px 10px',
                    fontSize: 12,
                    background:   days.includes(idx) ? 'var(--accent)'   : 'var(--surface2)',
                    borderColor:  days.includes(idx) ? 'var(--accent)'   : 'var(--border)',
                    color:        days.includes(idx) ? '#fff'             : 'var(--text)',
                    fontWeight:   days.includes(idx) ? 700                : 400,
                  }}
                >
                  {name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Monthly — day of month */}
        {freq === 'monthly' && (
          <div className="form-group">
            <label>Day of month</label>
            <select value={dayOfMonth} onChange={e => setDayOfMonth(Number(e.target.value))}>
              {Array.from({ length: 28 }, (_, i) => i + 1).map(d => (
                <option key={d} value={d}>{ordinal(d)}</option>
              ))}
            </select>
          </div>
        )}

        {/* Time + timezone */}
        {needsTime && (
          <>
            <div className="form-group">
              <label>Time</label>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <select
                  value={hour}
                  onChange={e => setHour(Number(e.target.value))}
                  style={{ flex: 1 }}
                >
                  {Array.from({ length: 24 }, (_, i) => i).map(h => {
                    const label = h === 0 ? '12 AM (midnight)'
                      : h < 12  ? `${h} AM`
                      : h === 12 ? '12 PM (noon)'
                      : `${h - 12} PM`
                    return <option key={h} value={h}>{pad2(h)}:00 — {label}</option>
                  })}
                </select>
                <span style={{ color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>:</span>
                <select
                  value={minute}
                  onChange={e => setMinute(Number(e.target.value))}
                  style={{ width: 90 }}
                >
                  {[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].map(m => (
                    <option key={m} value={m}>{pad2(m)}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="form-group">
              <label>Timezone</label>
              <select value={timezone} onChange={e => setTimezone(e.target.value)}>
                {tzList.map(tz => (
                  <option key={tz} value={tz}>
                    {tz === browserTZ && tz !== 'UTC' ? `${tz} (your browser)` : tz}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}

        {/* Preview */}
        <div style={{
          background: 'var(--bg)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '10px 14px',
          marginTop: 4,
          marginBottom: 20,
        }}>
          <div style={{ fontSize: 13, color: 'var(--text)', marginBottom: 6 }}>
            {description || '—'}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>cron:</span>
            <code style={{ fontSize: 13, color: 'var(--accent-2)', letterSpacing: '0.5px' }}>
              {cron || '—'}
            </code>
          </div>
          {freq !== 'every_minute' && freq !== 'every_n_minutes' && freq !== 'hourly' && (
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
              Scheduler runs in UTC. Times are stored as UTC cron.
            </div>
          )}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="primary"
            disabled={!cron || (freq === 'weekly' && days.length === 0)}
            onClick={() => cron && onApply(cron)}
          >
            Apply
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
