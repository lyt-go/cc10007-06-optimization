import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/rfm'
const columns = [
  "规则编号",
  "最近消费天界",
  "消费频次界",
  "消费金额界",
  "分层层级",
  "层间流转规则",
  "更新周期",
  "覆盖用户量"
]
const actions = [
  "提交规则评审",
  "启用规则",
  "停用规则"
]
const statuses = [
  "草稿",
  "规则评审中",
  "已启用",
  "已停用"
]
const stats = [
  {
    "label": "分层规则总数",
    "value": 0
  },
  {
    "label": "已启用规则",
    "value": 0
  },
  {
    "label": "评审中规则",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function RfmView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('分层规则列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'RFM分层列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('RFM分层动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'RFM分层操作失败')
    }
  }

  function toggleSelect(row: Row) {
    const id = Number(row.id)
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }

  async function batchSubmit() {
    setErrorMessage('')
    setNotice('')
    if (selectedIds.length === 0) {
      setErrorMessage('请先多选需要整组提交的分层规则记录')
      return
    }
    try {
      const response = await request(`${ENDPOINT}/batch-actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { ids: selectedIds } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || payload.detail || '整组提交未生效')
      }
      const detail = (payload.results ?? [])
        .map((item: Record<string, unknown>) => `${item['规则编号'] ?? item.id}：${item['message']}`)
        .join('；')
      setNotice(`${payload.message}${detail ? `（${detail}）` : ''}`)
      setSelectedIds([])
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '整组提交失败')
    }
  }

  return (
    <section className="page" data-module="rfm">
      <header className="page-head">
        <div>
          <h2>RFM分层管理</h2>
          <p className="page-desc">配置RFM用户价值分层规则，定义最近、频率、金额的分层阈值和层间流转逻辑。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('分层规则登记入口尚未接入审批流')}>
            登记分层规则
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出RFM分层清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
        <button className="btn primary" type="button" onClick={() => void batchSubmit()}>
          整组提交（已选 {selectedIds.length} 条）
        </button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            <th>选择</th>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              <td>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(Number(row.id))}
                  onChange={() => toggleSelect(row)}
                />
              </td>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无RFM分层数据，可先登记分层规则</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条RFM分层记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
