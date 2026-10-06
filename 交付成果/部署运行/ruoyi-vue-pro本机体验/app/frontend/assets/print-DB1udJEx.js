import{f as e}from"./utils-BZqUPeGF.js";import{c as t}from"./formatTime-C7ULsZ46.js";function n(e){let n=r(e.tableElement,`.el-table__header-wrapper thead`),a=r(e.tableElement,`.el-table__body-wrapper tbody`),o=r(e.tableElement,`.el-table__footer-wrapper tfoot`);if(!n||!a)throw Error(`未找到可打印的表格内容`);let s=t(new Date,`YYYY-MM-DD`),c=[...e.footerLabels||[],`打印日期：${s}`].map(e=>`<span>${i(e)}</span>`).join(``);return`<!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="UTF-8" />
        <title>${i(e.title)}</title>
        <style>
          * { box-sizing: border-box; }
          body { margin: 0; background: #eef0f3; color: #303133; font-family: Arial, "Microsoft YaHei", sans-serif; font-size: 14px; }
          .print-page { width: calc(100% - 32px); min-height: 210mm; margin: 16px auto; padding: 12mm; background: #fff; box-shadow: 0 2px 12px rgba(0, 0, 0, .12); }
          h1 { margin: 0; text-align: center; font-size: 28px; font-weight: 600; }
          .print-meta, .print-footer { display: flex; justify-content: space-between; gap: 20px; padding: 12px 0; }
          .print-meta span { flex: 1; }
          .print-meta span:nth-child(2) { text-align: center; }
          .print-meta span:last-child { text-align: right; }
          table { width: 100%; border-collapse: collapse; table-layout: auto; }
          th, td { min-width: 54px; padding: 7px 6px; border: 1px solid #303133; line-height: 1.5; vertical-align: middle; word-break: break-word; }
          th { text-align: center; font-weight: 600; background: #f5f7fa; }
          td.is-right, th.is-right { text-align: right; }
          td.is-center, th.is-center { text-align: center; }
          tr { page-break-inside: avoid; }
          .print-footer { padding-bottom: 0; color: #606266; font-size: 12px; }
          @page { size: A3 landscape; margin: 8mm; }
          @media print {
            body { background: #fff; }
            .print-page { width: auto; min-height: auto; margin: 0; padding: 0; box-shadow: none; }
          }
        </style>
      </head>
      <body>
        <main class="print-page">
          <h1>${i(e.title)}</h1>
          <div class="print-meta">
            <span>编制单位：${i(e.companyName)}</span>
            <span>${i(e.centerText)}</span>
            <span>${i(e.periodLabel)}</span>
          </div>
          <table>${n.outerHTML}${a.outerHTML}${o?.outerHTML||``}</table>
          <div class="print-footer">${c}</div>
        </main>
      </body>
    </html>`}function r(e,t){let n=e.querySelector(t);if(!n)return;let r=n.cloneNode(!0);return r.querySelectorAll(`button`).forEach(e=>{e.replaceWith(document.createTextNode(e.textContent||``))}),r.querySelectorAll(`svg, input, textarea, .el-icon, .caret-wrapper, .el-checkbox__input`).forEach(e=>e.remove()),r.querySelectorAll(`[title]`).forEach(e=>e.removeAttribute(`title`)),r}function i(t){return e(String(t??``))}export{i as n,n as t};