import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/churnincr'
const columns = [
  "明细编号",
  "批次编号",
  "用户段",
  "规则版本",
  "模型版本",
  "是否跨边界",
  "预警推送频率",
  "名单状态",
  "结算状态",
  "名单条数",
  "模型准确率",
  "更新时间"
]
const actions = [
  "提交核对",
  "确认生效",
  "归档明细"
]
const statuses = [
  "候选",
  "重算核对中",
  "已生效",
  "已归档"
]
const stats = [
  {
    "label": "增量名单明细总数",
    "value": 0
  },
  {
    "label": "稳定名单明细",
    "value": 0
  },
  {
    "label": "候选名单明细",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function ChurnincrView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [batchId, setBatchId] = useState('RFM-0002')
  const [verifyReport, setVerifyReport] = useState<Record<string, unknown> | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('流失预警增量名单读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流失预警增量名单读取失败')
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
        throw new Error('增量名单明细动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '增量名单明细操作失败')
    }
  }

  async function runBatchOp(path: string) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${path}`, {
        method: 'POST',
        body: JSON.stringify({ values: { 批次编号: batchId } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || payload.detail || '批次操作未生效')
      }
      setNotice(String(payload.message ?? '批次操作完成'))
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '批次操作失败')
    }
  }

  async function runVerify() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/verify/${encodeURIComponent(batchId)}`)
      if (!response.ok) {
        throw new Error('批次核对结果读取失败')
      }
      const payload = await response.json()
      setVerifyReport(payload as Record<string, unknown>)
      setNotice(`批次 ${batchId} 核对完成：${String(payload['核对结论'] ?? '')}`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '批次核对失败')
    }
  }

  return (
    <section className="page" data-module="churnincr">
      <header className="page-head">
        <div>
          <h2>流失预警增量名单</h2>
          <p className="page-desc">按变化边界定位受影响用户段做增量重算：仅更新跨过边界的用户，未受影响用户保留原预警推送频率，未生效版本只进入候选计算。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出增量名单清单
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

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void runBatchOp('generate') }}>
        <label className="filter-item">
          <span>批次核对对象</span>
          <input value={batchId} onChange={(event) => setBatchId(event.target.value)} placeholder="如 RFM-0002" />
        </label>
        <button className="btn primary" type="submit">生成增量名单</button>
        <button className="btn" type="button" onClick={() => void runBatchOp('confirm')}>确认重算完成</button>
        <button className="btn" type="button" onClick={() => void runVerify()}>批次核对</button>
      </form>

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
      </form>

      {verifyReport && (
        <table className="data-table">
          <thead>
            <tr>
              <th>核对项</th>
              <th>名单条数</th>
              <th>模型准确率</th>
              <th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            {((verifyReport['变更段'] as Row[]) ?? []).map((item) => (
              <tr key={`changed-${String(item['用户段'])}`}>
                <td>变更段 · {String(item['用户段'])}（{String(item['名单状态'])}）</td>
                <td>{String(item['名单条数'])}</td>
                <td>{String(item['模型准确率'])}</td>
                <td>{String(item['更新时间'])}</td>
              </tr>
            ))}
            {((verifyReport['未变更段'] as Row[]) ?? []).map((item) => (
              <tr key={`unchanged-${String(item['用户段'])}`}>
                <td>未变更段 · {String(item['用户段'])}（{String(item['名单状态'])}）</td>
                <td>{String(item['名单条数'])}</td>
                <td>{String(item['模型准确率'])}</td>
                <td>{String(item['更新时间'])}</td>
              </tr>
            ))}
            <tr>
              <td>合计（变更段 / 未变更段 / 可结算明细）</td>
              <td>{String(verifyReport['变更段条数'])} / {String(verifyReport['未变更段条数'])} / {String(verifyReport['可结算明细数'])}</td>
              <td colSpan={2}>{String(verifyReport['核对结论'])}</td>
            </tr>
          </tbody>
        </table>
      )}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无流失预警增量名单明细，可先生成增量名单</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条流失预警增量名单明细</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
