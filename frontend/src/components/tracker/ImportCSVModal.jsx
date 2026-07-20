/**
 * ImportCSVModal — paste historical bets as CSV, preview, and import.
 *
 * Expected columns (comma-separated, first row = header):
 *   date, match, market, odds, stake [, bookmaker, result, notes]
 *
 * "result" values: Won / Lost / Void / Pending (default: Pending)
 */
import { useState } from 'react'
import { X, Upload, AlertCircle, CheckCircle } from 'lucide-react'
import { bulkImportBets } from '../../api/tracker'

const EXAMPLE = `date,match,market,odds,stake,bookmaker,result,notes
2024-05-10,Man City vs Arsenal,Over 2.5,1.85,100,Betway,Won,derby day
2024-05-10,Liverpool vs Chelsea,BTTS Yes,1.72,50,Betway,Lost,`

function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/)
  if (lines.length < 2) return { rows: [], error: 'Need at least a header row and one data row.' }

  // Parse header
  const header = lines[0].split(',').map(h => h.trim().toLowerCase().replace(/\s+/g, '_'))
  const required = ['date', 'match', 'market', 'odds', 'stake']
  const missing  = required.filter(r => !header.includes(r))
  if (missing.length > 0) return { rows: [], error: `Missing required columns: ${missing.join(', ')}` }

  const rows = []
  const parseErrors = []

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue

    // Simple CSV split — handle quoted fields
    const cells = []
    let cur = ''
    let inQuote = false
    for (const ch of line) {
      if (ch === '"') { inQuote = !inQuote; continue }
      if (ch === ',' && !inQuote) { cells.push(cur); cur = ''; continue }
      cur += ch
    }
    cells.push(cur)

    const row = {}
    header.forEach((col, idx) => { row[col] = (cells[idx] || '').trim() })

    // Basic validation
    const odds  = parseFloat(row.odds)
    const stake = parseFloat(row.stake)
    if (!row.match) { parseErrors.push(`Row ${i}: match is required`); continue }
    if (!row.market) { parseErrors.push(`Row ${i}: market is required`); continue }
    if (isNaN(odds) || odds <= 1.0) { parseErrors.push(`Row ${i}: odds must be > 1.0`); continue }
    if (isNaN(stake) || stake <= 0) { parseErrors.push(`Row ${i}: stake must be > 0`); continue }

    rows.push({
      date:      row.date || '',
      match:     row.match,
      market:    row.market,
      odds,
      stake,
      bookmaker: row.bookmaker || 'Betway',
      result:    row.result    || 'Pending',
      notes:     row.notes     || '',
    })
  }

  return { rows, parseErrors }
}

export default function ImportCSVModal({ onClose, onImported }) {
  const [csvText, setCsvText]     = useState('')
  const [preview, setPreview]     = useState(null)
  const [parseErrors, setParseErrors] = useState([])
  const [importing, setImporting] = useState(false)
  const [result, setResult]       = useState(null)
  const [error, setError]         = useState(null)

  function handleParse() {
    const { rows, error: parseErr, parseErrors: rowErrors } = parseCSV(csvText)
    setParseErrors(rowErrors || [])
    if (parseErr) { setError(parseErr); setPreview(null); return }
    setError(null)
    setPreview(rows)
  }

  async function handleImport() {
    if (!preview?.length) return
    setImporting(true)
    setError(null)
    try {
      const res = await bulkImportBets(preview)
      setResult(res)
      onImported?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setImporting(false)
    }
  }

  const RESULT_COLOR = { Won: 'text-green-400', Lost: 'text-red-400', Void: 'text-slate-400', Pending: 'text-blue-400' }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl bg-[var(--bg)] border border-[var(--border)] rounded-2xl shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border)]">
          <div>
            <h2 className="text-base font-bold text-[var(--text-h)]">Import Bets from CSV</h2>
            <p className="text-xs text-[var(--text)] opacity-80 mt-0.5">
              Paste a CSV with columns: date, match, market, odds, stake (+ optional: bookmaker, result, notes)
            </p>
          </div>
          <button onClick={onClose} className="text-[var(--text)] opacity-70 hover:opacity-100 transition-opacity">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">

          {/* CSV textarea */}
          {!result && (
            <>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-semibold text-[var(--text-h)]">CSV Data</label>
                  <button
                    onClick={() => setCsvText(EXAMPLE)}
                    className="text-[10px] text-[var(--accent)] hover:underline"
                  >
                    Load example
                  </button>
                </div>
                <textarea
                  value={csvText}
                  onChange={e => { setCsvText(e.target.value); setPreview(null); setError(null) }}
                  rows={8}
                  placeholder={EXAMPLE}
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] text-xs font-mono text-[var(--text-h)] focus:outline-none focus:border-[var(--accent)] resize-none"
                />
              </div>

              {error && (
                <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
                  <AlertCircle size={12} className="shrink-0 mt-0.5" />
                  {error}
                </div>
              )}

              {parseErrors.length > 0 && (
                <div className="rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2 text-xs text-amber-400 space-y-0.5">
                  {parseErrors.map((e, i) => <p key={i}>{e}</p>)}
                </div>
              )}

              {/* Preview table */}
              {preview && preview.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-[var(--text-h)] mb-1.5">
                    Preview — {preview.length} row{preview.length !== 1 ? 's' : ''} ready to import
                  </p>
                  <div className="rounded-lg border border-[var(--border)] overflow-auto max-h-48">
                    <table className="w-full text-xs min-w-[600px]">
                      <thead>
                        <tr className="bg-[var(--code-bg)] border-b border-[var(--border)]">
                          {['Date', 'Match', 'Market', 'Odds', 'Stake', 'Bookmaker', 'Result'].map(h => (
                            <th key={h} className="px-3 py-2 text-left font-semibold text-[var(--text)] opacity-75">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.map((row, i) => (
                          <tr key={i} className="border-t border-[var(--border)]">
                            <td className="px-3 py-1.5 text-[var(--text)] opacity-75 font-mono">{row.date || '—'}</td>
                            <td className="px-3 py-1.5 text-[var(--text-h)] max-w-[160px] truncate">{row.match}</td>
                            <td className="px-3 py-1.5 text-[var(--text)]">{row.market}</td>
                            <td className="px-3 py-1.5 font-mono text-[var(--accent)]">{row.odds.toFixed(2)}</td>
                            <td className="px-3 py-1.5 font-mono text-[var(--text-h)]">{row.stake}</td>
                            <td className="px-3 py-1.5 text-[var(--text)] opacity-75">{row.bookmaker}</td>
                            <td className={`px-3 py-1.5 font-medium ${RESULT_COLOR[row.result] || ''}`}>{row.result}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {preview && preview.length === 0 && (
                <p className="text-xs text-[var(--text)] opacity-70 italic">No valid rows found. Check the CSV format.</p>
              )}
            </>
          )}

          {/* Import result */}
          {result && (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <CheckCircle size={36} className="text-green-400" />
              <div>
                <p className="text-base font-bold text-[var(--text-h)]">
                  {result.imported} bet{result.imported !== 1 ? 's' : ''} imported
                </p>
                {result.skipped > 0 && (
                  <p className="text-xs text-[var(--text)] opacity-80 mt-1">{result.skipped} row{result.skipped !== 1 ? 's' : ''} skipped (missing required fields)</p>
                )}
              </div>
              {result.errors?.length > 0 && (
                <div className="text-xs text-amber-400 space-y-0.5">
                  {result.errors.map((e, i) => <p key={i}>{e}</p>)}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-[var(--border)]">
          <button onClick={onClose} className="text-sm text-[var(--text)] opacity-65 hover:opacity-100 transition-opacity">
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <div className="flex items-center gap-2">
              {!preview && (
                <button
                  onClick={handleParse}
                  disabled={!csvText.trim()}
                  className="px-4 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-40 transition-colors"
                >
                  Preview
                </button>
              )}
              {preview && (
                <>
                  <button
                    onClick={() => { setPreview(null); setError(null) }}
                    className="px-4 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:bg-[var(--code-bg)] transition-colors"
                  >
                    Edit
                  </button>
                  <button
                    onClick={handleImport}
                    disabled={importing || preview.length === 0}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
                  >
                    <Upload size={13} />
                    {importing ? 'Importing…' : `Import ${preview.length} bet${preview.length !== 1 ? 's' : ''}`}
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
