export function operationsError(code, detail = '') {
  const message =
    Number(code) === 403
      ? '当前账号无权查看或操作此项功能，请联系平台管理员。'
      : '暂时无法读取或完成操作；如已提交处置，请先查询原操作结果。'
  const caseCode = String(detail).match(/\bCASE_[A-Z_]{1,64}\b/)?.[0]
  const memoryCode = String(detail).match(
    /\b(?:MEMORY_[A-Z_]{1,64}|REVISION_CONFLICT|VERSION_CONFLICT|RESULT_INVALIDATED|MEMORY_GONE)\b/
  )?.[0]
  return Object.assign(new Error(message), { code: Number(code), caseCode, memoryCode })
}
