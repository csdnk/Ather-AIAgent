import{i as e,s as t,t as n}from"./_plugin-vue_export-helper-BDArRvIa.js";import{Ca as r,J as i,La as a,Oa as o,X as s,Xa as c,da as l,fa as u,ga as d,gt as f,ha as p,ht as m,j as h,k as g,la as _,lo as v,no as y,p as b,ra as x,so as S,va as C}from"./form-create-WSkr8cvM.js";import{r as w}from"./useCache-C73L5ijB.js";import{t as T}from"./useMessage-BQQzQgcx.js";import{t as E}from"./Dialog-BcMKjUfB.js";import{c as D,i as O,l as k}from"./format-miWoxyLH.js";import{n as A}from"./print-DB1udJEx.js";var j=t(i()),M={A4:{width:210,height:297},B5:{width:176,height:250}},N=8,P=4,F={paperType:`B5`,orientation:`landscape`,width:250,height:176,marginLeft:0,marginTop:0,fontSize:16};function I(e,t,n){let{width:r,height:i}=H(n),a=N+n.marginTop,o=N+n.marginLeft;return V(`凭证打印`,R(t).map(t=>z(e,t)).join(``),`
        @page { size: ${r}mm ${i}mm; margin: ${a}mm ${N}mm ${N}mm ${o}mm; }
        .voucher-page { width: ${r}mm; min-height: ${i}mm; padding: 8mm; }
      `,n.fontSize)}function L(e,t,n){let r=n.map(B).join(``);return V(`凭证列表`,`
    <section class="voucher-list-page">
      <h1>凭证列表</h1>
      <div class="list-meta"><span>编制单位：${A(e)}</span><span>${A(t)}</span></div>
      <table class="voucher-list-table">
        <thead><tr><th>日期</th><th>凭证字号</th><th>摘要</th><th>科目</th><th>借方金额</th><th>贷方金额</th><th>制单人</th><th>审核人</th></tr></thead>
        <tbody>${r}</tbody>
      </table>
    </section>
  `,`@page { size: A3 landscape; margin: 8mm; } .voucher-list-page { width: 100%; padding: 4mm; }`,14)}function R(e){let t=[];return e.forEach(e=>{let n=Math.max(1,Math.ceil(e.entries.length/P));for(let r=0;r<n;r++){let i=e.entries.slice(r*P,r*P+P);for(;i.length<P;)i.push(void 0);t.push({voucher:e,entries:i,currentPage:r+1,totalPages:n})}}),t}function z(e,t){let{voucher:n,entries:r}=t,i=r.map(e=>`
        <tr>
          <td>${A(e?.digest)}</td>
          <td>${A(e?U(e):``)}</td>
          <td class="money">${e?.debitAmount?A(O(e.debitAmount)):``}</td>
          <td class="money">${e?.creditAmount?A(O(e.creditAmount)):``}</td>
        </tr>
      `).join(``);return`
    <section class="voucher-page">
      <h1>记账凭证</h1>
      <div class="title-double-line"></div>
      <div class="attachment-count">附单据&nbsp;&nbsp;${n.attachmentCount||``}&nbsp;&nbsp;张</div>
      <div class="voucher-meta">
        <span>单位：${A(e)}</span>
        <span>日期：${(0,j.default)(n.voucherTime).format(`YYYY年MM月DD日`)}</span>
        <span>凭证号：${A(n.voucherWordName)}-${n.voucherNumber}（${t.currentPage}/${t.totalPages}）</span>
      </div>
      <table class="voucher-table">
        <thead><tr><th>摘要</th><th>会计科目</th><th>借方金额</th><th>贷方金额</th></tr></thead>
        <tbody>${i}</tbody>
        <tfoot><tr><td colspan="2">合计：${A(k(Number(n.debitAmount)))}</td><td class="money">${A(O(n.debitAmount))}</td><td class="money">${A(O(n.creditAmount))}</td></tr></tfoot>
      </table>
      <div class="voucher-footer"><span>财务主管：</span><span>审核：${A(n.reviewerUserName)}</span><span>出纳：</span><span>制单：${A(n.creatorUserName)}</span></div>
    </section>
  `}function B(e){return`
    <tr>
      <td>${(0,j.default)(e.voucherTime).format(`YYYY-MM-DD`)}</td>
      <td>${A(e.voucherWordName)}-${e.voucherNumber}</td>
      <td>${e.entries.map(e=>`<div>${A(e.digest)}</div>`).join(``)}</td>
      <td>${e.entries.map(e=>`<div>${A(U(e))}</div>`).join(``)}</td>
      <td class="money">${e.entries.map(e=>`<div>${e.debitAmount?A(O(e.debitAmount)):``}</div>`).join(``)}</td>
      <td class="money">${e.entries.map(e=>`<div>${e.creditAmount?A(O(e.creditAmount)):``}</div>`).join(``)}</td>
      <td>${A(e.creatorUserName)}</td>
      <td>${A(e.reviewerUserName)}</td>
    </tr>
  `}function V(e,t,n,r){return`<!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="UTF-8" />
        <title>${A(e)}</title>
        <style>
          * { box-sizing: border-box; }
          body { margin: 0; background: #eef0f3; color: #303133; font-family: Arial, "Microsoft YaHei", sans-serif; font-size: ${r}px; }
          ${n}
          .voucher-page, .voucher-list-page { box-sizing: border-box; margin: 16px auto; background: #fff; box-shadow: 0 2px 12px rgba(0, 0, 0, .12); page-break-after: always; }
          h1 { margin: 0; text-align: center; font-size: 30px; font-weight: 500; }
          .title-double-line { width: 200px; height: 6px; margin: 8px auto; border-top: 1px solid; border-bottom: 1px solid; }
          .attachment-count { margin-bottom: 6px; text-align: right; }
          .voucher-meta, .voucher-footer, .list-meta { display: flex; justify-content: space-between; gap: 16px; padding: 7px 0; }
          table { width: 100%; border-collapse: collapse; }
          th, td { border: 1px solid #303133; padding: 10px 8px; vertical-align: middle; }
          .voucher-table th:nth-child(1) { width: 28%; }
          .voucher-table th:nth-child(2) { width: 38%; }
          .voucher-table th:nth-child(3), .voucher-table th:nth-child(4) { width: 17%; }
          .voucher-table tbody tr { height: 54px; }
          .money { text-align: right; }
          .voucher-footer span { width: 25%; }
          .voucher-footer span:nth-child(2), .voucher-footer span:nth-child(3) { text-align: center; }
          .voucher-footer span:last-child { text-align: right; }
          .voucher-list-page h1 { margin-bottom: 12px; font-weight: 600; }
          .voucher-list-table th, .voucher-list-table td { padding: 8px 6px; }
          .voucher-list-table tr { page-break-inside: avoid; }
          @media print {
            body { background: #fff; }
            .voucher-page, .voucher-list-page { width: auto; min-height: auto; margin: 0; padding: 0; box-shadow: none; }
          }
        </style>
      </head>
      <body>
        <main>${t}</main>
      </body>
    </html>`}function H(e){let t=e.paperType===`CUSTOM`?{width:e.width,height:e.height}:M[e.paperType],n=Math.min(t.width,t.height),r=Math.max(t.width,t.height);return e.orientation===`landscape`?{width:r,height:n}:{width:n,height:r}}function U(e){return D(e.subjectCode,e.subjectName,e.auxiliaries.map(e=>e.name))}r(),y();var W=C({name:`FmsVoucherPrintForm`,__name:`FmsVoucherPrintForm`,setup(e,{expose:t}){let{wsCache:n}=w(),r=T(),i=v(!1),a=v(0),s=v(``),c=v([]),l=v({...F}),u=S({paperType:[{required:!0,message:`请选择打印类型`,trigger:`change`},{validator:(e,t,n)=>{if(l.value.paperType!==`CUSTOM`||l.value.width&&l.value.height){n();return}n(Error(`请输入自定义纸张的宽度和长度`))},trigger:`change`}]}),d=v(),f=v();function p(e,t,r){a.value=e,s.value=t,c.value=r,l.value={...F,...n.get(_(e))},i.value=!0,o(()=>d.value?.clearValidate())}async function m(){d.value&&await d.value.validate()&&(n.set(_(a.value),l.value),await h(I(s.value,c.value,l.value)),i.value=!1)}async function h(e){let t=f.value?.contentDocument,n=f.value?.contentWindow;!t||!n||(t.open(),t.write(e),t.close(),await t.fonts?.ready,n.focus(),n.print())}function g(e){let t=window.open(``,`_blank`);if(!t){r.warning(`浏览器阻止了新窗口，请允许弹出窗口后重试`);return}t.document.open(),t.document.write(e),t.document.close(),t.focus()}function _(e){return`fmsVoucherPrintSetting:${e}`}t({open:p,printHtml:h,previewHtml:g});let y={wsCache:n,message:r,dialogVisible:i,accountSetId:a,companyName:s,vouchers:c,formData:l,formRules:u,formRef:d,printIframeRef:f,open:p,submitForm:m,printHtml:h,previewHtml:g,getStorageKey:_};return Object.defineProperty(y,"__isScriptSetup",{enumerable:!1,value:!0}),y}}),G=e({default:()=>$});r();var K={key:0,class:`mt-10px flex items-center gap-20px`},q={class:`flex items-center gap-8px [&_.el-input-number]:!w-72px`},J={class:`flex items-center gap-8px [&_.el-input-number]:!w-72px`},Y={class:`flex items-center gap-20px`},X={class:`flex items-center gap-8px [&_.el-input-number]:!w-72px`},Z={class:`flex items-center gap-8px [&_.el-input-number]:!w-72px`},Q={class:`flex items-center gap-8px [&_.el-input-number]:!w-72px`},ee={ref:`printIframeRef`,class:`pointer-events-none fixed -left-9999px top-0 h-1px w-1px border-0 opacity-0`,title:`凭证打印`};function te(e,t,n,r,i,o){let v=g,y=h,S=b,C=f,w=m,T=s,D=E;return a(),u(x,null,[d(D,{modelValue:r.dialogVisible,"onUpdate:modelValue":t[8]||=e=>r.dialogVisible=e,title:`凭证打印`,width:`500px`},{footer:c(()=>[d(T,{type:`primary`,onClick:r.submitForm},{default:c(()=>[...t[23]||=[p(`保存并打印`,-1)]]),_:1}),d(T,{onClick:t[7]||=e=>r.dialogVisible=!1},{default:c(()=>[...t[24]||=[p(`取 消`,-1)]]),_:1})]),default:c(()=>[d(w,{ref:`formRef`,model:r.formData,rules:r.formRules,"label-position":`top`,class:`[&_.el-form-item]:!mb-12px [&_.el-form-item__label]:!pb-2px`},{default:c(()=>[d(C,{label:`打印类型`,prop:`paperType`},{default:c(()=>[d(y,{modelValue:r.formData.paperType,"onUpdate:modelValue":t[0]||=e=>r.formData.paperType=e},{default:c(()=>[d(v,{value:`A4`},{default:c(()=>[...t[9]||=[p(`A4`,-1)]]),_:1}),d(v,{value:`B5`},{default:c(()=>[...t[10]||=[p(`B5`,-1)]]),_:1}),d(v,{value:`CUSTOM`},{default:c(()=>[...t[11]||=[p(`自定义纸张`,-1)]]),_:1})]),_:1},8,[`modelValue`]),r.formData.paperType===`CUSTOM`?(a(),u(`div`,K,[_(`div`,q,[t[12]||=_(`span`,null,`宽度`,-1),d(S,{modelValue:r.formData.width,"onUpdate:modelValue":t[1]||=e=>r.formData.width=e,controls:!1,min:1},null,8,[`modelValue`]),t[13]||=_(`span`,null,`毫米`,-1)]),_(`div`,J,[t[14]||=_(`span`,null,`长度`,-1),d(S,{modelValue:r.formData.height,"onUpdate:modelValue":t[2]||=e=>r.formData.height=e,controls:!1,min:1},null,8,[`modelValue`]),t[15]||=_(`span`,null,`毫米`,-1)])])):l(``,!0)]),_:1}),d(C,{label:`图像方向`},{default:c(()=>[d(y,{modelValue:r.formData.orientation,"onUpdate:modelValue":t[3]||=e=>r.formData.orientation=e},{default:c(()=>[d(v,{value:`portrait`},{default:c(()=>[...t[16]||=[p(`纵向`,-1)]]),_:1}),d(v,{value:`landscape`},{default:c(()=>[...t[17]||=[p(`横向`,-1)]]),_:1})]),_:1},8,[`modelValue`])]),_:1}),d(C,{label:`边框调整`},{default:c(()=>[_(`div`,Y,[_(`div`,X,[t[18]||=_(`span`,null,`左`,-1),d(S,{modelValue:r.formData.marginLeft,"onUpdate:modelValue":t[4]||=e=>r.formData.marginLeft=e,controls:!1,min:0},null,8,[`modelValue`]),t[19]||=_(`span`,null,`毫米`,-1)]),_(`div`,Z,[t[20]||=_(`span`,null,`上`,-1),d(S,{modelValue:r.formData.marginTop,"onUpdate:modelValue":t[5]||=e=>r.formData.marginTop=e,controls:!1,min:0},null,8,[`modelValue`]),t[21]||=_(`span`,null,`毫米`,-1)])])]),_:1}),d(C,{label:`字体大小`},{default:c(()=>[_(`div`,Q,[d(S,{modelValue:r.formData.fontSize,"onUpdate:modelValue":t[6]||=e=>r.formData.fontSize=e,controls:!1,min:12,max:24},null,8,[`modelValue`]),t[22]||=_(`span`,null,`像素`,-1)])]),_:1})]),_:1},8,[`model`,`rules`])]),_:1},8,[`modelValue`]),_(`iframe`,ee,null,512)],64)}var $=n(W,[[`render`,te],[`__file`,`E:/projects/codex/.agent-work/aether/workspace-support/ruoyi-local-20261006/frontend/src/views/fms/voucher/components/FmsVoucherPrintForm.vue`]]);export{G as n,L as r,$ as t};