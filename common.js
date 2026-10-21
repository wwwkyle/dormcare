/* DormCare · 宿舍智能报修与反馈系统 — shared client chrome
 *
 * Loaded by ALL FOUR pages (menu + student + technician + administrator)
 * before the page's own module.
 * Exposes exactly one global, `DC`, holding:
 *
 *   DC.t / DC.fill / DC.lang / DC.setLang / DC.I18N   — i18n
 *   DC.esc / DC.md                                    — safe renderers
 *   DC.api / DC.post / DC.busy / DC.setMsg            — transport + UI state
 *   DC.dayLabel / DC.slotAt / DC.slotTimeText         — date & slot helpers
 *   DC.cfg / DC.loadConfig / DC.loadWorkers / DC.loadTickets
 *   DC.health / DC.ready                              — key + connection state
 *   DC.me / DC.loadMe / DC.requireRole / DC.logout    — who is signed in
 *   DC.on / DC.afterLang                              — page hooks
 *
 * The sign-in page loads it too; each of them then loads exactly one module of
 * its own on top.
 *
 * The OpenRouter key lives in the server process memory only; nothing here
 * ever sees it. Bilingual by construction: the whole chrome switches between
 * 中文 and English, and the chosen language is passed to the server so the AI
 * agents answer in the same language.
 */
window.DC = (function () {
  "use strict";

  /* ====================================================================== */
  /*  i18n                                                                  */
  /* ====================================================================== */

  var I18N = {
    zh: {
      htmlLang: "zh-CN",
      title: "DormCare · 宿舍智能报修与反馈系统",
      studentTitle: "学生端 · 报修申请",
      studentSub: "DormCare 宿舍智能报修系统",
      workerTitle: "师傅端 · 排班与派活",
      workerSub: "DormCare 维修调度工作台",
      brandSub: "宿舍智能报修与反馈系统",
      statusChecking: "连接中…",
      statusReady: "在线 · {t} 张工单 / {w} 位师傅",
      statusNeedKey: "AI 未启用（可手动填写）",
      statusDown: "无法连接服务端",
      roleStudent: "学生",
      roleWorker: "师傅",
      logout: "退出登录",
      backMenu: "主菜单",
      workerIdentityBar: "工号 {id} · 仅显示派给你的工单",
      /* ---- 主菜单 / the main menu ---- */
      menuTitle: "DormCare 主菜单",
      menuSub: "选择要进入的功能",
      menuHello: "你好，{name}",
      menuRoleHint: "你以{role}身份登录，以下是你可以进入的功能。切换账号或角色需要先退出登录。",
      menuStudentReport: "我要报修",
      menuStudentReportSub: "AI 智能受理，填写损坏类型与空闲时段",
      menuStudentRecords: "我的报修记录",
      menuStudentRecordsSub: "查看进度、上门时间与反馈评价",
      menuStudentAccount: "账号设置",
      menuStudentAccountSub: "更正宿舍信息、修改登录密码",
      menuWorkerPlan: "排班与派活",
      menuWorkerPlanSub: "生成当日路线、查看时间轴与未排入工单",
      menuWorkerPool: "我的工单",
      menuWorkerPoolSub: "接单、完工，以及处理未能排入的工单",
      menuWorkerAccount: "账号设置",
      menuWorkerAccountSub: "修改登录密码",
      menuAdminOverview: "运行总览",
      menuAdminOverviewSub: "统计卡与按状态／紧急度／书院的分布",
      menuAdminTickets: "全部工单",
      menuAdminTicketsSub: "全系统报修单，含报修人与联系方式",
      menuAdminUsers: "账号管理",
      menuAdminUsersSub: "更正宿舍、重置密码、发放新账号",
      menuAdminRoster: "维修班组",
      menuAdminRosterSub: "师傅名册、工种与可承接类别",
      menuAdminAccount: "账号设置",
      menuAdminAccountSub: "修改登录密码",
      menuStudentOnly: "仅学生可更正宿舍",
      /* ---- 每个详情页的标题 / one title per detail page ---- */
      studentReportTitle: "我要报修 · DormCare",
      studentRecordsTitle: "我的报修记录 · DormCare",
      workerPlanTitle: "排班与派活 · DormCare",
      workerJobsTitle: "我的工单 · DormCare",
      adminOverviewTitle: "运行总览 · DormCare",
      adTicketPageTitle: "全部工单 · DormCare",
      adUsersPageTitle: "账号管理 · DormCare",
      adRosterPageTitle: "维修班组 · DormCare",
      adRosterHint: "班组名册决定排班引擎把哪一类活派给谁。发放新的师傅账号请到「账号管理」。",
      accPwTitleOnly: "修改密码 · DormCare",
      accNoRoom: "你的账号没有宿舍信息（师傅与管理员不是住户），这里只能修改密码。",
      /* sign in / register */
      loginTitle: "DormCare 登录",
      loginSub: "宿舍智能报修与反馈系统",
      tabLogin: "登录",
      tabRegister: "学生注册",
      loginPaneHint: "学生、师傅、管理员都从这里进入，凭账号自动进入各自的界面。",
      lblAccount: "账号",
      phAccount: "学号（学生）/ 工号（师傅）/ admin（管理员）",
      lblPassword: "密码",
      phPassword: "请输入密码",
      btnLogin: "登录",
      regHint: "只有学生可以自助注册。师傅账号由物业统一发放，管理员账号由系统预置，都不开放注册。",
      lblRegName: "姓名",
      phRegName: "如 王小明",
      lblRegCollege: "所属书院",
      regCollegeNone: "请选择书院",
      lblRegDorm: "宿舍号",
      regDormNote: "报修地点取自这里登记的宿舍，注册后每次报修无需再填。搬宿舍请联系物业更新。",
      regPassword2: "确认密码",
      btnRegister: "注册并登录",
      demoTitle: "演示账号",
      demoHint: "这是一组预置账号，登录后可立刻看到两种角色的界面差异。",
      demoRole: "角色",
      demoAccount: "账号",
      demoPassword: "密码",
      demoRoom: "报修地点",
      demoStudentRole: "学生",
      demoWorkerRole: "师傅",
      authWelcome: "登录成功，正在进入{role}界面…",
      authCreated: "注册成功，已自动登录。",
      auth_bad_credentials: "账号或密码不正确。",
      auth_username_taken: "该账号已被注册。",
      auth_missing_username: "请输入账号。",
      auth_bad_username: "账号只能包含字母、数字、点、下划线和短横线。",
      auth_missing_password: "请输入密码。",
      auth_weak_password: "密码至少 6 位。",
      auth_missing_name: "请输入姓名。",
      auth_need_password: "两次输入的密码不一致。",
      auth_too_many: "登录尝试过于频繁，请稍后再试。",
      err_forbidden: "你没有权限访问该内容。",
      err_delete_not_allowed: "工单已进入维修流程，无法删除，请联系物业前台。",
      err_bad_rating: "评分需在 0-5 之间。",
      err_bad_origin: "请求来源不被信任，已拒绝。",
      /* ticket status + appointment */
      tkWorker: "负责师傅",
      tkAppointment: "预计上门",
      tkNoWorker: "等待派单",
      tkNoAppointment: "当日班次已满，待重新安排",
      studentEtaNote: "已排定：{w} 将于 {d} {a} 上门，请保持联系畅通。",
      studentOffRouteNote: "{w} 已接下这一单：今天的路线排不下它，师傅会在顺路时过来处理。",
      reschedOffer: "师傅建议改约到 {d}（沿用你已选的空闲时段），你同意吗？",
      reschedNote: "师傅留言",
      btnReschedAccept: "接受新时间",
      btnReschedDecline: "暂不接受",
      reschedAccepted: "已改约，系统会重新安排这一单。",
      reschedDeclined: "已回复师傅：暂不改约，原时间不变。",
      tkFeedback: "学生反馈",
      /* feedback loop */
      fbTitle: "维修反馈",
      fbHint: "修好了吗？你的评分会直接影响这张工单是否结案。",
      fbRating: "你的评分",
      fbPlaceholder: "还有什么想说的？（选填，例如「师傅很准时，但排水管接口还在渗水」）",
      fbSubmit: "提交反馈",
      fbThanks: "感谢反馈，工单已结案。",
      fbReopened: "已记录，低分将把工单重新放回待办队列。",
      fbCurrent: "已提交反馈",
      fbStar: "{n} 星",
      btnAccept: "接单",
      btnComplete: "完成维修",
      btnConfirm: "确认完成",
      btnReopen: "还没修好",
      minutes: "分钟",
      tagRag: "RAG",
      tagAiFilled: "AI 已填充",
      /* intake */
      intakeTitle: "AI 智能受理",
      intakeHint: "文字、语音和图片都进入同一个多轮助手；只有确认草稿后才会创建工单。",
      stepDescribe: "描述问题",
      stepAssist: "AI 追问 / 建议",
      stepDraft: "工单草稿",
      stepConfirm: "确认提交",
      intakeClearBtn: "清空",
      aiDetected: "识别结果",
      aiClass: "报修类别",
      aiPriority: "紧急度",
      aiReason: "判定依据",
      aiConfidence: "置信度",
      aiSource: "判定来源",
      sourceModel: "模型判定",
      sourceRules: "关键词规则",
      confHigh: "高",
      confMedium: "中",
      confLow: "低",
      certNeeded: "需持证技师",
      partsNeeded: "需备件",
      outOfScope: "超出范围",
      aiAskTitle: "还需要你补充几点",
      aiAskFill: "把这些填进故障描述，逐条回答",
      aiTipsTitle: "师傅上门前，你可以先试试",
      /* form */
      formTitle: "草稿编辑与手工报修",
      manualFallbackHint: "可核对并修改 AI 草稿；AI 或语音不可用时，也可直接手工填写并提交。",
      manualOpen: "改为手动填写",
      lblTitle: "工单标题",
      phTitle: "简要概括故障",
      lblType: "损坏类型",
      lblCollege: "书院",
      lblBlock: "楼栋 / 座",
      lblDorm: "宿舍号",
      phDorm: "如 A-301 / 3 栋 301",
      lblAddress: "报修地点",
      lblPublicArea: "公共区域报修",
      addrLockedNote: "地址来自你的账号登记，无需每次填写。",
      addrUnknown: "账号尚未登记宿舍",
      lblPublicWhere: "公共区域位置",
      phPublicWhere: "如「3 楼走廊东侧」「洗衣房」",
      addrPublicNote: "公共区域（走廊灯、洗衣房、公共卫生间等）不受账号宿舍限制。",
      tagPublic: "公共区域",
      needPublicWhere: "请说明公共区域的具体位置。",
      blkAuto: "该书院未记录细分楼栋，宿舍号请写全（如「3 栋 301」）。",
      lblPriority: "紧急度",
      lblDate: "期望维修日期",
      lblSlots: "空闲时间（可多选，每格 30 分钟）",
      lblSlotsNote: "点击或拖动选择；系统只会把这一单排进你勾选的时段",
      slotClear: "清空",
      slotMorning: "上午",
      slotAfternoon: "下午",
      slotAll: "全天",
      slotNone: "尚未选择空闲时间",
      slotPicked: "已选 {n} 个时段：{list}",
      lblName: "姓名（选填）",
      phName: "如 王同学",
      lblContact: "联系方式（选填）",
      phContact: "手机号 / 微信 / 邮箱",
      lblDesc: "故障描述（选填）",
      phDesc: "补充说明，例如「下午三点后我在宿舍」",
      speechStart: "语音转文字",
      speechHint: "语音结果进入上方同一输入框，可编辑后发送。",
      speechListening: "正在聆听…说完后会填入输入框。",
      speechUnsupported: "当前浏览器不支持语音识别，请直接输入。",
      speechFailed: "语音识别未授权或失败，请直接输入。",
      photoChoose: "拍照 / 相册",
      photoFallback: "你仍可只用文字描述并提交。",
      reportPhotos: "故障照片",
      completionPhotos: "完工照片",
      completedAt: "完成时间",
      photoRules: "仅 JPG/PNG，每张不超过 5MB，最多 5 张。",
      photoInvalid: "仅支持每张不超过 5MB 的 JPG/PNG。",
      photoTooMany: "最多上传 5 张图片。",
      agentPlaceholder: "继续描述故障或回答助手追问…",
      agentSend: "发送给 AI",
      agentConfirm: "确认草稿并创建",
      agentYou: "你",
      agentAssistant: "受理助手",
      agentImagesOnly: "已附加 {n} 张图片",
      agentDraftSummary: "AI 工单草稿",
      missingTitle: "还需补充",
      selfHelpTitle: "可安全尝试的处理方法",
      agentResolved: "已解决，无需报修",
      agentNeedRepair: "仍需师傅，生成工单",
      agentFallback: "AI 暂时不可用。你可以重试，或点击“改为手动填写”继续报修。",
      routeSelfHelp: "A 提供维修建议",
      routeRepair: "B 生成工单",
      emergencyRepair: "立即报修",
      kbEmpty: "知识库当前为空",
      confidenceLabel: "置信度",
      draftRestored: "已恢复上次受理草稿。",
      submitBtn: "手动提交报修",
      needType: "请选择损坏类型。",
      needSlots: "请至少选择一个空闲时段。",
      needDorm: "请填写宿舍号。",
      submitOk: "提交成功，单号 {id}。已进入师傅的排班池。",
      submitOkRedirect: "提交成功，单号 {id}。正在打开「我的报修记录」…",
      submitFail: "提交失败，请重试。",
      myTicketsTitle: "我的报修记录",
      myTicketsHint: "只显示你自己提交的单——点击任意一条可展开详情。",
      refresh: "刷新",
      expandAll: "展开全部",
      collapseAll: "收起全部",
      /* ---- 账号设置 / account settings ---- */
      accTitle: "账号设置",
      accHint: "搬宿舍了或者密码需要更换，都可以在这里自己改，改完立刻生效。已提交的工单仍保留提交时的地址。",
      accRoomTitle: "我的宿舍",
      accRoomHint: "报修地点取自这里。改完之后新提交的工单会落在新地址，已经派出去的单不会被改写——师傅已经在路上了。",
      accCurrentRoom: "当前登记",
      accPwTitle: "修改密码",
      accPwHint: "需要先输入当前密码。改完之后，其他设备上的登录会全部失效，当前这台设备保持登录。",
      accCurrentPw: "当前密码",
      accNewPw: "新密码",
      accNewPw2: "确认新密码",
      accSaveRoom: "保存宿舍信息",
      accSavePw: "修改密码",
      accRoomOk: "宿舍信息已更新，之后提交的工单会寄到新地址。",
      accPwOk: "密码已修改，其他设备的登录已失效。",
      accPwMismatch: "两次输入的新密码不一致。",
      auth_bad_current: "当前密码不正确。",
      auth_same_password: "新密码与当前密码相同。",
      /* worker */
      dispatchTitle: "AI 智能派活",
      dispatchHint: "系统用确定性算法按「固定工时 + 书院路程规则 + 学生空闲时间」自动排班，再由 AI 依据知识库生成逐单作业指引。",
      lblWorker: "维修师傅",
      lblWorkDate: "排班日期",
      lblFilterCollege: "路线筛选",
      allColleges: "全部书院",
      optimizeBtn: "⚡ 生成 AI 排班",
      dispatchBtn: "AI 逐单派活说明",
      rationaleBtn: "排班依据",
      timelineTitle: "当日路线时间轴",
      poolTitleMine: "我的工单",
      poolHintMine: "系统按工种与持证要求派给你的工单——别人的活不会出现在这里。",
      needOptimizeFirst: "请先生成排班。",
      /* stats */
      stJobs: "当日工单",
      stWork: "维修工时",
      stTravel: "路程耗时",
      stIdle: "空余时间",
      stUnsched: "未能排入",
      stFirst: "首单开始",
      stLast: "末单结束",
      unitMin: "分钟",
      unitJob: "单",
      unitNone: "—",
      /* timeline */
      travelFromWorkshop: "从维修间出发 {m} 分钟",
      travelSame: "同书院搬迁 {m} 分钟",
      travelCross: "跨书院搬迁 {m} 分钟",
      travelBack: "返回维修间约 {m} 分钟",
      stops: "{n} 站",
      schedRange: "{a} — {b}",
      unschedTitle: "以下工单未能排入当天路线",
      unschedHint: "不是排错了，是它们真的塞不进当天的班次。你有两种处理方式：顺路去修（不改动已排好的路线），或者给学生一个改约建议，学生会收到提示。",
      unschedWhy: "排不进去的原因",
      unschedPickDate: "建议改到哪天",
      btnTakeEnRoute: "顺路去修",
      btnSuggestReschedule: "建议改约",
      unschedTaken: "已接单 · 顺路处理",
      unschedSuggested: "已发出改约建议",
      offrouteTaken: "已接下这一单，会顺路过去处理；学生端已同步提示。",
      offrouteSuggested: "改约建议已发出，等待学生确认。",
      reasonNoTime: "当日班次已排满，建议改约次日或增派师傅",
      reasonWindow: "未能落在学生声明的空闲时段内，建议电话协调改约",
      reasonShort: "学生声明的空闲时段短于该类维修的固定工时，需请学生放宽时间",
      reasonNone: "学生未登记任何空闲时间，需先联系学生补充",
      tkFeedback: "学生反馈",
      aiMode: "AI",
      aiFallback: "规则",
      aiCardTitle: "AI 作业指引",
      /* ticket list */
      tkCollege: "书院",
      tkBlock: "楼栋",
      tkDorm: "宿舍",
      tkDate: "日期",
      tkWindow: "空闲",
      tkContact: "联系",
      tkDuration: "固定工时",
      tkPriority: "紧急度",
      tkDesc: "故障描述",
      tkStatus: "状态",
      tkFiled: "提交",
      emptyTickets: "暂无报修记录。",
      emptyPool: "暂无待排工单。",
      detail: "详情",
      /* setup */
      setupTitle: "启用模型密钥",
      setupBody: "服务端还没有模型密钥，AI 功能暂不可用（其余功能按固定规则正常运行）。把 OpenRouter 密钥填入下方即可启用。密钥只保存在<strong>服务器进程内存</strong>中：不写入任何文件、不进入前端、不会回显。",
      setupPlaceholder: "粘贴 OpenRouter 密钥…",
      setupSubmit: "启用",
      setupWorking: "正在服务器上校验…",
      setup_ok: "密钥已启用并通过校验，AI 功能已就绪。",
      setup_unverified: "密钥已保存在服务端，但刚才无法连接模型校验。你调用 AI 时仍会使用它。",
      setup_rejected: "密钥被服务商拒绝（401/403），未写入。请检查后重试。",
      setup_not_a_key: "这看起来不是有效的密钥。",
      setup_missing_key: "请输入密钥。",
      setup_already_enrolled: "服务端已登记过密钥。",
      setup_disabled_env: "服务端已通过环境变量配置密钥，页面登记已停用。",
      setupNetErr: "网络错误，未能提交。",
      /* errors */
      err_network: "网络连接失败，请检查网络后重试。",
      err_generic: "出了点问题，请稍后再试。",
      err_empty_text: "请输入故障描述。",
      err_bad_college: "书院无效，请重新选择。",
      err_bad_dorm: "请填写宿舍号。",
      err_account_no_address: "你的账号还没登记宿舍，请联系管理员补全。",
      err_auth_missing_college: "请选择所属书院。",
      err_auth_need_block: "请选择所在楼栋。",
      err_auth_missing_dorm: "请填写宿舍号。",
      err_bad_date: "日期无效。",
      err_bad_slots: "请至少选择一个有效的空闲时段。",
      err_bad_status: "状态无效。",
      err_not_found: "找不到对应记录。",
      err_bad_json: "请求格式有误。",
      err_bad_size: "请求内容过大。",
      err_too_many: "请求过于频繁，请稍等片刻。",
      err_busy: "模型当前繁忙，请稍后再试。",
      err_upstream: "模型暂时无法连接，请稍后再试。",
      err_key_inactive: "模型密钥当前未生效，请检查密钥。",
      err_server: "服务端出现异常，请稍后再试。",
      err_need_key: "AI 功能需要模型密钥，请先启用密钥。",
      /* ---- 管理员端 / administrator ---- */
      roleAdmin: "管理员",
      adminTitle: "管理员端 · 运行总览",
      adminSub: "DormCare 运维管理台",
      adminIdentityBar: "运维账号 {id}",
      adTotalTickets: "全部工单",
      adStudents: "学生账号",
      adWorkersCount: "师傅账号",
      adAdmins: "管理员",
      adRoster: "班组人数",
      adToday: "今日工单",
      adTodayOpen: "今日待办",
      adAvgRating: "平均评分",
      adByStatus: "按状态",
      adByPriority: "按紧急度",
      adByCollege: "按书院",
      adNoData: "暂无数据",
      adTicketSection: "全部工单",
      adTicketHint: "系统内所有学生的报修单。这里能看到报修人是谁——学生端与师傅端都看不到对方的这些字段。",
      adStatusFilter: "状态筛选",
      adFilterAll: "全部状态",
      adReload: "刷新",
      adNoTickets: "暂无工单。",
      adOwner: "报修人",
      adAccount: "账号",
      adContact: "联系",
      adAssign: "负责师傅",
      adRating: "评分",
      adDelete: "删除",
      adDeleteConfirm: "确认删除这张工单？此操作不可撤销。",
      adDeleted: "工单已删除。",
      adUsersSection: "账号管理",
      adUsersHint: "更正登记错误的宿舍、重置密码、发放新账号。宿舍号是每张工单的报修地址，学生自己改不了，只能由这里更正。",
      adUserCount: "{n} 个账号",
      adNewStudent: "新建学生",
      adNewWorker: "发放师傅账号",
      adCreateStudent: "新建学生账号",
      adCreateWorker: "发放师傅账号",
      adSave: "保存",
      adCancel: "取消",
      adSaved: "已保存。",
      adResetPw: "重置密码",
      adNewPw: "新密码（至少 6 位）",
      adPwReset: "密码已重置，该账号之前的登录会话已全部吊销。",
      adWorkerId: "工号",
      adTradeZh: "工种（中文）",
      adTradeEn: "工种（英文）",
      adCertified: "持证技师",
      adPhone: "内部分机（选填）",
      adSpecialties: "可承接的报修类别",
      adCreate: "创建",
      adCreatedUser: "账号已创建。",
      adCreatedWorker: "师傅账号已发放，可用该工号登录师傅端。",
      adRoom: "报修地点",
      adNoRoom: "未登记宿舍",
      adWorkersSection: "维修班组",
      adTrade: "工种",
      adTicketCount: "{n} 单",
      err_nothing_to_update: "没有需要修改的内容。",
      err_need_specialty: "请至少选择一个可承接的报修类别。",
      err_worker_taken: "该工号的师傅档案已存在。"
    },

    en: {
      htmlLang: "en",
      title: "DormCare · Smart Dorm Repair & Feedback System",
      studentTitle: "Student · File a repair",
      studentSub: "DormCare smart dorm repair system",
      workerTitle: "Technician · Schedule & dispatch",
      workerSub: "DormCare maintenance dispatch console",
      brandSub: "Smart dorm repair & feedback system",
      statusChecking: "Connecting…",
      statusReady: "Online · {t} tickets / {w} technicians",
      statusNeedKey: "AI off (manual entry still works)",
      statusDown: "Cannot reach the server",
      roleStudent: "Student",
      roleWorker: "Technician",
      logout: "Sign out",
      backMenu: "Main menu",
      workerIdentityBar: "Staff {id} · only your own jobs are shown",
      /* ---- the main menu ---- */
      menuTitle: "DormCare main menu",
      menuSub: "Choose what you would like to open",
      menuHello: "Hello, {name}",
      menuRoleHint: "You are signed in as {role}. These are the sections you may open; to switch account or role, sign out first.",
      menuStudentReport: "Report a fault",
      menuStudentReportSub: "AI intake, damage type and your free time",
      menuStudentRecords: "My repair records",
      menuStudentRecordsSub: "Progress, arrival time and your feedback",
      menuStudentAccount: "Account settings",
      menuStudentAccountSub: "Correct your room, change your password",
      menuWorkerPlan: "Schedule & dispatch",
      menuWorkerPlanSub: "Build the day's route, timeline and unplaced jobs",
      menuWorkerPool: "My jobs",
      menuWorkerPoolSub: "Accept jobs, mark them done, handle the unplaced ones",
      menuWorkerAccount: "Account settings",
      menuWorkerAccountSub: "Change your password",
      menuAdminOverview: "Operations overview",
      menuAdminOverviewSub: "Scorecards and breakdowns by status, priority and college",
      menuAdminTickets: "All tickets",
      menuAdminTicketsSub: "Every request in the system, with who filed it",
      menuAdminUsers: "Accounts",
      menuAdminUsersSub: "Correct rooms, reset passwords, issue accounts",
      menuAdminRoster: "Technician roster",
      menuAdminRosterSub: "Who is on the team, their trade and coverage",
      menuAdminAccount: "Account settings",
      menuAdminAccountSub: "Change your password",
      menuStudentOnly: "Only a student can correct a room",
      /* ---- one title per detail page ---- */
      studentReportTitle: "Report a fault · DormCare",
      studentRecordsTitle: "My repair records · DormCare",
      workerPlanTitle: "Schedule & dispatch · DormCare",
      workerJobsTitle: "My jobs · DormCare",
      adminOverviewTitle: "Operations overview · DormCare",
      adTicketPageTitle: "All tickets · DormCare",
      adUsersPageTitle: "Accounts · DormCare",
      adRosterPageTitle: "Technician roster · DormCare",
      adRosterHint: "The roster decides which jobs the scheduling engine may assign to whom. Issue a technician account from Accounts.",
      accPwTitleOnly: "Change password · DormCare",
      accNoRoom: "Your account has no room on file (technicians and administrators are not residents), so only the password can be changed here.",
      loginTitle: "Sign in to DormCare",
      loginSub: "Smart dorm repair & feedback system",
      tabLogin: "Sign in",
      tabRegister: "Student sign-up",
      loginPaneHint: "Students, technicians and administrators all sign in here; the account decides which console you land on.",
      lblAccount: "Account",
      phAccount: "Student number / staff number / admin",
      lblPassword: "Password",
      phPassword: "Enter your password",
      btnLogin: "Sign in",
      regHint: "Only students can register here. Technician accounts are issued by the maintenance office and administrator accounts are seeded — neither is open for sign-up.",
      lblRegName: "Name",
      phRegName: "e.g. Alex Wong",
      lblRegCollege: "College",
      regCollegeNone: "Choose a college",
      lblRegDorm: "Room number",
      regDormNote: "Repair addresses come from the room recorded here, so you will not retype it on every request. Ask the maintenance office to update it if you move.",
      regPassword2: "Confirm password",
      btnRegister: "Register and sign in",
      demoTitle: "Demo accounts",
      demoHint: "Seeded accounts — sign in as either role to see how far apart the two consoles are.",
      demoRole: "Role",
      demoAccount: "Account",
      demoPassword: "Password",
      demoRoom: "Repair address",
      demoStudentRole: "Student",
      demoWorkerRole: "Technician",
      authWelcome: "Signed in — taking you to the {role} console…",
      authCreated: "Account created and signed in.",
      auth_bad_credentials: "Incorrect account or password.",
      auth_username_taken: "That account is already registered.",
      auth_missing_username: "Please enter your account.",
      auth_bad_username: "An account may only contain letters, digits, dot, underscore and hyphen.",
      auth_missing_password: "Please enter your password.",
      auth_weak_password: "A password needs at least 6 characters.",
      auth_missing_name: "Please enter your name.",
      auth_need_password: "The two passwords do not match.",
      auth_too_many: "Too many sign-in attempts. Please try again later.",
      err_forbidden: "You do not have access to that.",
      err_delete_not_allowed: "This job is already under way, so it cannot be deleted. Please contact the maintenance office.",
      err_bad_rating: "A rating must be between 0 and 5.",
      err_bad_origin: "The request came from an untrusted origin and was refused.",
      tkWorker: "Technician",
      tkAppointment: "Appointed",
      tkNoWorker: "Awaiting assignment",
      tkNoAppointment: "The shift is full — a new time is being arranged",
      studentEtaNote: "Scheduled: {w} will arrive on {d} at {a}. Please keep your phone reachable.",
      studentOffRouteNote: "{w} has taken this job on: it does not fit today's route, so the technician will come by en route.",
      reschedOffer: "The technician suggests moving this to {d}, keeping the free time you already picked. Agree?",
      reschedNote: "Note from the technician",
      btnReschedAccept: "Accept the new time",
      btnReschedDecline: "Not now",
      reschedAccepted: "Moved — the system will schedule this again.",
      reschedDeclined: "The technician has been told you would rather keep the original time.",
      tkFeedback: "Student feedback",
      fbTitle: "Repair feedback",
      fbHint: "Is it fixed? Your rating decides whether this ticket is closed for good.",
      fbRating: "Your rating",
      fbPlaceholder: "Anything else? (optional, e.g. \"on time, but the drain joint still seeps\")",
      fbSubmit: "Submit feedback",
      fbThanks: "Thanks — the ticket is now closed.",
      fbReopened: "Noted — a low rating puts the job back in the queue.",
      fbCurrent: "Feedback submitted",
      fbStar: "{n} star(s)",
      btnAccept: "Accept job",
      btnComplete: "Mark complete",
      btnConfirm: "Confirm fixed",
      btnReopen: "Not fixed yet",
      minutes: "min",
      tagRag: "RAG",
      tagAiFilled: "Filled by AI",
      intakeTitle: "AI intake",
      intakeHint: "Text, speech and images all enter one multi-turn assistant; no ticket is created until you confirm its draft.",
      stepDescribe: "Describe",
      stepAssist: "AI questions / advice",
      stepDraft: "Ticket draft",
      stepConfirm: "Confirm",
      intakeClearBtn: "Clear",
      aiDetected: "Result",
      aiClass: "Category",
      aiPriority: "Priority",
      aiReason: "Reasoning",
      aiConfidence: "Confidence",
      aiSource: "Decided by",
      sourceModel: "model",
      sourceRules: "keyword rules",
      aiAskTitle: "A few things we still need",
      aiAskFill: "Paste these into the description and answer them",
      aiTipsTitle: "Worth trying before the technician arrives",
      confHigh: "high",
      confMedium: "medium",
      confLow: "low",
      certNeeded: "Certified tech required",
      partsNeeded: "Parts",
      outOfScope: "Out of scope",
      formTitle: "Edit draft or report manually",
      manualFallbackHint: "Review and edit the AI draft here, or fill and submit it manually if AI or speech is unavailable.",
      manualOpen: "Fill manually",
      lblTitle: "Ticket title",
      phTitle: "Summarize the fault",
      lblType: "Damage / repair type",
      lblCollege: "College",
      lblBlock: "Block / tower",
      lblDorm: "Dorm number",
      phDorm: "e.g. A-301 / Block 3, Room 301",
      lblAddress: "Repair address",
      lblPublicArea: "Public area",
      addrLockedNote: "The address comes from your account — no need to retype it.",
      addrUnknown: "No room on file for this account",
      lblPublicWhere: "Where is the public area?",
      phPublicWhere: "e.g. \"east end of the 3rd-floor corridor\", \"laundry room\"",
      addrPublicNote: "Shared areas — corridor lights, laundry room, shared bathroom — are not bound to your own room.",
      tagPublic: "Public area",
      needPublicWhere: "Please describe where the public area is.",
      blkAuto: "No sub-blocks are recorded for this college — please give the full room reference (e.g. \"Block 3, Room 301\").",
      lblPriority: "Priority",
      lblDate: "Preferred date",
      lblSlots: "Free time (multi-select, 30-minute blocks)",
      lblSlotsNote: "Click or drag to select — the job will only be scheduled inside the blocks you pick",
      slotClear: "Clear",
      slotMorning: "Morning",
      slotAfternoon: "Afternoon",
      slotAll: "All day",
      slotNone: "No free time selected yet",
      slotPicked: "{n} block(s) selected: {list}",
      lblName: "Name (optional)",
      phName: "e.g. Alex Wong",
      lblContact: "Contact (optional)",
      phContact: "Phone / WeChat / email",
      lblDesc: "Description (optional)",
      phDesc: "Anything else, e.g. \"I'm in the dorm after 3pm\"",
      speechStart: "Speech to text",
      speechHint: "Speech enters the same input above and remains editable before sending.",
      speechListening: "Listening… the transcript will appear in the input.",
      speechUnsupported: "Speech recognition is unavailable; please type instead.",
      speechFailed: "Speech permission was denied or recognition failed; please type instead.",
      photoChoose: "Camera / photos",
      photoFallback: "You can still describe and submit the issue using text.",
      reportPhotos: "Fault photos",
      completionPhotos: "Completion photos",
      completedAt: "Completed at",
      photoRules: "JPG/PNG only, up to 5 MB each and 5 images.",
      photoInvalid: "Use JPG/PNG images no larger than 5 MB each.",
      photoTooMany: "Up to 5 images are allowed.",
      agentPlaceholder: "Describe the fault or answer the assistant…",
      agentSend: "Send to AI",
      agentConfirm: "Confirm draft and create",
      agentYou: "You",
      agentAssistant: "Intake assistant",
      agentImagesOnly: "{n} image(s) attached",
      agentDraftSummary: "AI ticket draft",
      missingTitle: "Still needed",
      selfHelpTitle: "Safe steps to try",
      agentResolved: "Resolved — no ticket needed",
      agentNeedRepair: "Still need help — create draft",
      agentFallback: "AI is temporarily unavailable. Retry, or choose “Fill manually” to continue.",
      routeSelfHelp: "A Repair advice",
      routeRepair: "B Create ticket",
      emergencyRepair: "Report now",
      kbEmpty: "Knowledge base is currently empty",
      confidenceLabel: "Confidence",
      draftRestored: "Your previous intake draft was restored.",
      submitBtn: "Submit manually",
      needType: "Please pick a repair type.",
      needSlots: "Please select at least one free-time block.",
      needDorm: "Please enter the dorm number.",
      submitOk: "Submitted — ticket {id}. It is now in the technician's queue.",
      submitOkRedirect: "Submitted — ticket {id}. Opening your repair records…",
      submitFail: "Submission failed. Please try again.",
      myTicketsTitle: "My repair records",
      myTicketsHint: "Only the tickets you filed are listed — click any row to expand it.",
      refresh: "Refresh",
      expandAll: "Expand all",
      collapseAll: "Collapse all",
      /* ---- account settings ---- */
      accTitle: "Account settings",
      accHint: "Moved rooms, or just want a different password — both are yours to change, and both take effect at once. Tickets you already filed keep the address they were filed with.",
      accRoomTitle: "My room",
      accRoomHint: "This is the address repairs are filed against. After a change, new requests go to the new room; jobs already on the road are left alone, because a technician is already on the way there.",
      accCurrentRoom: "Currently on file",
      accPwTitle: "Change password",
      accPwHint: "Your current password is required. Once it changes, every other device is signed out; this one stays signed in.",
      accCurrentPw: "Current password",
      accNewPw: "New password",
      accNewPw2: "Confirm new password",
      accSaveRoom: "Save room",
      accSavePw: "Change password",
      accRoomOk: "Room updated — new requests will be filed at the new address.",
      accPwOk: "Password changed; every other device has been signed out.",
      accPwMismatch: "The two new passwords do not match.",
      auth_bad_current: "That is not your current password.",
      auth_same_password: "The new password is the same as the old one.",
      dispatchTitle: "AI dispatch",
      dispatchHint: "A deterministic engine schedules the day from fixed durations, college travel rules and the students' declared free time. The AI then turns that plan into job-by-job guidance.",
      lblWorker: "Technician",
      lblWorkDate: "Schedule date",
      lblFilterCollege: "Route filter",
      allColleges: "All colleges",
      optimizeBtn: "⚡ Generate AI schedule",
      dispatchBtn: "AI job-by-job briefing",
      rationaleBtn: "Why this plan",
      timelineTitle: "Route timeline",
      poolTitleMine: "My jobs",
      poolHintMine: "Tickets the engine assigned to you by trade and certification — nobody else's work shows up here.",
      needOptimizeFirst: "Generate a schedule first.",
      stJobs: "Jobs today",
      stWork: "Work time",
      stTravel: "Travel time",
      stIdle: "Idle time",
      stUnsched: "Not schedulable",
      stFirst: "First start",
      stLast: "Last finish",
      unitMin: "min",
      unitJob: "jobs",
      unitNone: "—",
      travelFromWorkshop: "{m} min from the workshop",
      travelSame: "same college · {m} min",
      travelCross: "cross-college · {m} min",
      travelBack: "~{m} min back to the workshop",
      stops: "{n} stops",
      schedRange: "{a} — {b}",
      unschedTitle: "These tickets could not be placed today",
      unschedHint: "Not a scheduling error — they genuinely do not fit the shift. You have two options: take it anyway en route (today's optimised route is left untouched), or send the student a rebooking suggestion, which they will see.",
      unschedWhy: "Why it did not fit",
      unschedPickDate: "Suggest a day",
      btnTakeEnRoute: "Take it en route",
      btnSuggestReschedule: "Suggest a new time",
      unschedTaken: "Accepted · on the way",
      unschedSuggested: "Suggestion sent",
      offrouteTaken: "Taken on as an en-route job — the student has been told.",
      offrouteSuggested: "Rebooking suggestion sent; waiting for the student.",
      reasonNoTime: "The shift is full — rebook to the next working day or send a second technician",
      reasonWindow: "Falls outside the student's declared free time — call to rebook",
      reasonShort: "The declared free time is shorter than this repair's fixed duration — ask the student to widen it",
      reasonNone: "The student declared no free time — contact them first",
      tkFeedback: "Student feedback",
      aiMode: "AI",
      aiFallback: "rules",
      aiCardTitle: "AI work guidance",
      tkCollege: "College",
      tkBlock: "Block",
      tkDorm: "Dorm",
      tkDate: "Date",
      tkWindow: "Free",
      tkContact: "Contact",
      tkDuration: "Fixed",
      tkPriority: "Priority",
      tkDesc: "Description",
      tkStatus: "Status",
      tkFiled: "Filed",
      emptyTickets: "No repair records yet.",
      emptyPool: "No tickets waiting.",
      detail: "Details",
      setupTitle: "Enable the model key",
      setupBody: "The server has no model key yet, so the AI features are unavailable (everything rules-based still runs). Paste the OpenRouter key below to enable them. The key is held in <strong>server process memory</strong> only: never written to a file, never in the front-end, never echoed back.",
      setupPlaceholder: "Paste the OpenRouter key…",
      setupSubmit: "Enable",
      setupWorking: "Verifying on the server…",
      setup_ok: "Key enrolled and verified. AI features are ready.",
      setup_unverified: "Key enrolled, but the model could not be reached to verify it just now. It will still be used when you call the AI.",
      setup_rejected: "The provider rejected this key (401/403). It has not been enrolled — check the value and try again.",
      setup_not_a_key: "That does not look like a valid key.",
      setup_missing_key: "Please enter a key.",
      setup_already_enrolled: "A key is already enrolled on this server.",
      setup_disabled_env: "This server was started with a key in its environment, so page enrollment is disabled.",
      setupNetErr: "Network error — the key was not submitted.",
      err_network: "Network error — please check your connection and try again.",
      err_generic: "Something went wrong. Please try again in a moment.",
      err_empty_text: "Please describe the fault.",
      err_bad_college: "Invalid college — please pick again.",
      err_bad_dorm: "Please enter the dorm number.",
      err_account_no_address: "Your account has no room on file yet — please ask the administrator.",
      err_auth_missing_college: "Please choose your college.",
      err_auth_need_block: "Please choose your tower.",
      err_auth_missing_dorm: "Please give your room number.",
      err_bad_date: "Invalid date.",
      err_bad_slots: "Please select at least one valid free-time block.",
      err_bad_status: "Invalid status.",
      err_not_found: "That record was not found.",
      err_bad_json: "The request was malformed.",
      err_bad_size: "The request was too large.",
      err_too_many: "Too many requests in a short time. Please wait a moment.",
      err_busy: "The model is busy right now. Please try again shortly.",
      err_upstream: "The model could not be reached. Please try again shortly.",
      err_key_inactive: "The model key is not active right now — please check it.",
      err_server: "Something went wrong on the server. Please try again.",
      err_need_key: "AI features need a model key. Please enable one in the panel above.",
      /* ---- administrator ---- */
      roleAdmin: "Administrator",
      adminTitle: "Administrator · Operations overview",
      adminSub: "DormCare operations console",
      adminIdentityBar: "Operations account {id}",
      adTotalTickets: "All tickets",
      adStudents: "Student accounts",
      adWorkersCount: "Technician accounts",
      adAdmins: "Administrators",
      adRoster: "Roster size",
      adToday: "Jobs today",
      adTodayOpen: "Open today",
      adAvgRating: "Average rating",
      adByStatus: "By status",
      adByPriority: "By priority",
      adByCollege: "By college",
      adNoData: "Nothing yet",
      adTicketSection: "All tickets",
      adTicketHint: "Every repair request in the system. Here you can see who filed it — the student console and the technician console never see each other's versions of these fields.",
      adStatusFilter: "Filter by status",
      adFilterAll: "All statuses",
      adReload: "Refresh",
      adNoTickets: "No tickets yet.",
      adOwner: "Filed by",
      adAccount: "Account",
      adContact: "Contact",
      adAssign: "Technician",
      adRating: "Rating",
      adDelete: "Delete",
      adDeleteConfirm: "Delete this ticket? This cannot be undone.",
      adDeleted: "Ticket deleted.",
      adUsersSection: "Accounts",
      adUsersHint: "Correct a room that was entered wrong, reset a password, issue a new account. The room is the address every ticket is filed against; a student cannot edit it, so it is fixed here.",
      adUserCount: "{n} accounts",
      adNewStudent: "New student",
      adNewWorker: "Issue a technician account",
      adCreateStudent: "New student account",
      adCreateWorker: "Issue a technician account",
      adSave: "Save",
      adCancel: "Cancel",
      adSaved: "Saved.",
      adResetPw: "Reset password",
      adNewPw: "New password (6+ characters)",
      adPwReset: "Password reset; every existing session for that account has been revoked.",
      adWorkerId: "Staff number",
      adTradeZh: "Trade (Chinese)",
      adTradeEn: "Trade (English)",
      adCertified: "Certified technician",
      adPhone: "Internal extension (optional)",
      adSpecialties: "Repair categories covered",
      adCreate: "Create",
      adCreatedUser: "Account created.",
      adCreatedWorker: "Technician account issued — it can sign in with that staff number.",
      adRoom: "Repair address",
      adNoRoom: "No room on file",
      adWorkersSection: "Technician roster",
      adTrade: "Trade",
      adTicketCount: "{n} tickets",
      err_nothing_to_update: "There is nothing to update.",
      err_need_specialty: "Pick at least one repair category the technician covers.",
      err_worker_taken: "A technician record with that staff number already exists."
    }
  };

  var LANG_KEY = "dormcare.lang";
  var LANG = "zh";
  var L = I18N.zh;

  /*: The one front door. Mirrors auth.MENU_PATH on the server. */
  var MENU = "/menu.html";

  /* ====================================================================== */
  /*  shared state                                                          */
  /* ====================================================================== */

  var SLOT_COUNT = 17;        // 08:00 .. 16:30
  var SLOT_START_HOUR = 8;
  var STEP = 30;
  var LUNCH_FROM = 8;         // slot index 8 == 12:00
  var LUNCH_TO = 11;          // slot index 11 == 13:30

  var DC = {
    I18N: I18N,
    MENU: MENU,                // the one front door every role lands on
    SLOT_COUNT: SLOT_COUNT,
    SLOT_START_HOUR: SLOT_START_HOUR,
    STEP: STEP,
    LUNCH_FROM: LUNCH_FROM,
    LUNCH_TO: LUNCH_TO,
    cfg: null,                // /api/config payload
    tickets: [],
    ready: false,             // server holds a usable model key
    lastPlan: null
  };

  var langHooks = [];         // page modules that need a repaint on language change

  /* ====================================================================== */
  /*  language                                                              */
  /* ====================================================================== */

  Object.defineProperty(DC, "lang", { get: function () { return LANG; } });

  function pickLang() {
    try {
      var saved = localStorage.getItem(LANG_KEY);
      if (saved === "zh" || saved === "en") return saved;
    } catch (e) { /* private mode */ }
    var nav = (navigator.language || navigator.userLanguage || "").toLowerCase();
    return nav.indexOf("zh") === 0 ? "zh" : (nav ? "en" : "zh");
  }

  /* ====================================================================== */
  /*  i18n plumbing                                                         */
  /* ====================================================================== */

  function t(key) {
    return (L[key] !== undefined) ? L[key] : (I18N.zh[key] || key);
  }
  DC.t = t;

  function fill(str, params) {
    if (!params) return str;
    return str.replace(/\{(\w+)\}/g, function (m, k) {
      return (params[k] !== undefined) ? params[k] : m;
    });
  }
  DC.fill = fill;

  /**
   * Server errors carry a stable `code`; the message is produced here so both
   * consoles are localised from the same table. Auth codes are keyed by their
   * own name (`auth_bad_credentials`), older API errors by an `err_` prefix
   * (`err_bad_college`) — try both before falling back.
   */
  function errorText(data) {
    data = data || {};
    if (data.code) {
      var bare = t(data.code);
      if (bare !== data.code) return bare;
      var byCode = t("err_" + data.code);
      if (byCode !== "err_" + data.code) return byCode;
    }
    return data.error || t("err_generic");
  }
  DC.errorText = errorText;

  /* ====================================================================== */
  /*  safe renderers                                                        */
  /* ====================================================================== */

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  DC.esc = esc;

  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  /** Minimal, safe Markdown rendering for model output. */
  function md(text) {
    var lines = esc(text || "").split(/\r?\n/);
    var out = [], list = null;
    function closeList() { if (list) { out.push("</" + list + ">"); list = null; } }
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      var m;
      if ((m = ln.match(/^\s*(#{1,4})\s+(.*)$/))) {
        closeList();
        var lvl = Math.min(4, m[1].length + 1);
        out.push("<h" + lvl + ">" + inline(m[2]) + "</h" + lvl + ">");
      } else if ((m = ln.match(/^\s*[-*·]\s+(.*)$/))) {
        if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
        out.push("<li>" + inline(m[1]) + "</li>");
      } else if ((m = ln.match(/^\s*(\d+)[.)]\s+(.*)$/))) {
        if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
        out.push("<li>" + inline(m[2]) + "</li>");
      } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(ln)) {
        closeList(); out.push("<hr>");
      } else if (!ln.trim()) {
        closeList();
      } else {
        closeList();
        out.push("<p>" + inline(ln) + "</p>");
      }
    }
    closeList();
    return out.join("");
  }
  DC.md = md;

  /* ====================================================================== */
  /*  API                                                                   */
  /* ====================================================================== */

  function api(path, options) {
    return fetch(path, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        // A session can die while a page is open (expiry, or a sign-out in
        // another tab). Bounce to the sign-in page instead of leaving an empty
        // console on screen.
        if (r.status === 401 && !/^\/api\/auth\/(login|register|me)/.test(path)) {
          window.location.replace("/login.html");
        }
        return { ok: r.ok, status: r.status, data: d };
      });
    });
  }
  DC.api = api;

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }
  DC.post = post;

  function busy(btn, on) {
    if (!btn) return;
    btn.classList.toggle("busy", !!on);
    btn.disabled = !!on;
  }
  DC.busy = busy;

  function setMsg(node, code, state) {
    if (!node) return;
    node.dataset.code = code || "";
    node.dataset.state = state || "";
    node.textContent = code ? t(code) : "";
  }
  DC.setMsg = setMsg;

  /* ====================================================================== */
  /*  date + time helpers                                                   */
  /* ====================================================================== */

  var WD_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  var WD_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MO_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function parseDay(iso) {
    var p = String(iso).split("-");
    return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  }

  function dayLabel(iso) {
    if (!iso) return "";
    var d = parseDay(iso);
    if (LANG === "zh") return (d.getMonth() + 1) + "月" + d.getDate() + "日 " + WD_ZH[d.getDay()];
    return WD_EN[d.getDay()] + ", " + MO_EN[d.getMonth()] + " " + d.getDate();
  }
  DC.dayLabel = dayLabel;

  function hhmm(mins) {
    return ("0" + Math.floor(mins / 60)).slice(-2) + ":" + ("0" + (mins % 60)).slice(-2);
  }

  function slotTimeText(i) {
    var mins = SLOT_START_HOUR * 60 + i * STEP;
    return hhmm(mins) + "-" + hhmm(mins + STEP);
  }
  DC.slotTimeText = slotTimeText;
  DC.slotAt = slotTimeText;

  function slotFromMinute(minute) {
    return ("0" + Math.floor(minute / 60)).slice(-2) + ":" + ("0" + (minute % 60)).slice(-2);
  }
  DC.hhmm = hhmm;
  DC.slotFromMinute = slotFromMinute;

  /* ====================================================================== */
  /*  loaders                                                               */
  /* ====================================================================== */

  function loadConfig() {
    return api("/api/config").then(function (res) {
      if (!res.ok) return null;
      DC.cfg = res.data;
      return DC.cfg;
    });
  }
  DC.loadConfig = loadConfig;

  function loadWorkers() {
    return api("/api/workers").then(function (res) {
      if (!res.ok) return [];
      return res.data.workers || [];
    });
  }
  DC.loadWorkers = loadWorkers;

  function loadTickets() {
    return api("/api/tickets?lang=" + encodeURIComponent(LANG)).then(function (res) {
      if (!res.ok) return DC.tickets;
      DC.tickets = res.data.tickets || [];
      return DC.tickets;
    });
  }
  DC.loadTickets = loadTickets;

  function refreshHealth() {
    return api("/api/health").then(function (res) {
      var d = res.data || {};
      DC.ready = !!d.key_configured;
      if (DC.ready) setStatus("ready", "statusReady", { t: d.tickets, w: d.workers });
      else setStatus("warn", "statusNeedKey");
      var setup = document.getElementById("setup");
      // Enrolling a key requires being signed in, so the panel is only offered
      // to someone the server would actually accept it from.
      if (setup) setup.hidden = !(d.enrollable && DC.me);
      return d;
    }).catch(function () {
      setStatus("down", "statusDown");
    });
  }
  DC.refreshHealth = refreshHealth;

  function setStatus(state, key, params) {
    var box = document.getElementById("status");
    var txt = document.getElementById("statusText");
    if (!box || !txt) return;
    box.dataset.state = state;
    txt.textContent = fill(t(key), params);
  }
  DC.setStatus = setStatus;

  /* ====================================================================== */
  /*  identity                                                              */
  /*                                                                        */
  /*  The server decides what you may see; this is only the browser's echo   */
  /*  of that decision. The pages themselves are already gated server-side,  */
  /*  so nothing here is load-bearing for security — it just makes the       */
  /*  interface honest about who you are.                                    */
  /* ====================================================================== */

  function loadMe() {
    return api("/api/auth/me").then(function (res) {
      DC.me = (res.ok && res.data && res.data.user) ? res.data.user : null;
      return DC.me;
    }).catch(function () {
      DC.me = null;
      return null;
    });
  }
  DC.loadMe = loadMe;

  function displayName(user) {
    if (!user) return "";
    if (langIsZh()) return user.name || user.name_en || user.username;
    return user.name_en || user.name || user.username;
  }
  DC.displayName = displayName;

  function langIsZh() { return LANG === "zh"; }

  function roleLabel(role) {
    if (role === "worker") return t("roleWorker");
    if (role === "admin") return t("roleAdmin");
    return t("roleStudent");
  }
  DC.roleLabel = roleLabel;

  function renderUser() {
    var chip = document.getElementById("userChip");
    if (!chip) return;
    if (!DC.me) { chip.hidden = true; return; }
    chip.hidden = false;
    var name = displayName(DC.me);
    var id = DC.me.worker_id || DC.me.username || "";
    // The label is written here rather than declared as data-i18n: this element
    // is rebuilt by renderUser() on every language change, so a static i18n
    // pass over it would be overwritten anyway. One source of truth.
    chip.innerHTML =
      '<span class="who"><span class="who-role ' + esc(DC.me.role) + '">' +
      esc(roleLabel(DC.me.role)) + "</span>" +
      '<span class="who-name">' + esc(name) + "</span>" +
      (id ? '<code class="who-id">' + esc(id) + "</code>" : "") +
      "</span>" +
      '<a class="btn tiny ghost" href="' + MENU + '">' +
      '<span aria-hidden="true">☰</span> ' + esc(t("backMenu")) + "</a>" +
      '<button type="button" class="btn tiny ghost" id="logoutBtn">' +
      esc(t("logout")) + "</button>";
    var btn = document.getElementById("logoutBtn");
    if (btn) btn.addEventListener("click", logout);
  }
  DC.renderUser = renderUser;

  function logout() {
    post("/api/auth/logout", {}).then(function () {
      window.location.replace("/login.html");
    }).catch(function () {
      window.location.replace("/login.html");
    });
  }
  DC.logout = logout;

  /**
   * Last line of defence for a role page: ask the server who we are and, if
   * the answer is not the role this page belongs to, go where we belong.
   *
   * `home` is the main menu for everybody — sign-in opens one front door and
   * the menu decides what this person may then enter. A role page reached the
   * wrong way therefore lands on the menu, not on another console.
   */
  var HOME = { student: MENU, worker: MENU, admin: MENU };

  function requireRole(role) {
    return loadMe().then(function (user) {
      if (!user) {
        window.location.replace("/login.html");
        return null;
      }
      if (role && user.role !== role) {
        window.location.replace(HOME[user.role] || "/login.html");
        return null;
      }
      renderUser();
      return user;
    });
  }
  DC.requireRole = requireRole;
  DC.homeFor = function (role) { return HOME[role] || "/login.html"; };

  /* ====================================================================== */
  /*  shared render helpers                                                */
  /*                                                                        */
  /*  Small PURE functions used by more than one page module. They live     */
  /*  here because each console was split into one page per menu card, and  */
  /*  duplicating a priority lookup or a "逸夫书院 · 东座 1 栋 · A-301"     */
  /*  formatter into nine files is how the copies drift apart.              */
  /* ====================================================================== */

  /** Fill a <select> from an HTML option list, preserving the current pick. */
  function fillSelect(sel, html) {
    if (!sel) return;
    var keep = sel.value;
    sel.innerHTML = html;
    // A browser selects the first option implicitly; set it explicitly so the
    // value is defined as soon as the options exist, and so a stale pick that
    // is no longer in the list falls back to the first entry rather than "".
    var opts = sel.options || [];
    var values = [];
    for (var i = 0; i < opts.length; i++) values.push(opts[i].value);
    sel.value = (keep && values.indexOf(keep) >= 0)
      ? keep
      : (values.length ? values[0] : "");
  }
  DC.fillSelect = fillSelect;

  /** Priority id -> the label in the current language. */
  function priLabel(p) {
    var info = (DC.cfg && DC.cfg.priorities && DC.cfg.priorities[p]) || {};
    return info[DC.lang] || p || "";
  }
  DC.priLabel = priLabel;

  /** College id -> its display name, falling back to the raw id. */
  function collegeLabel(id) {
    var hit = ((DC.cfg && DC.cfg.colleges) || []).filter(function (c) {
      return c.id === id;
    })[0];
    if (!hit) return id || "";
    return DC.lang === "zh" ? hit.zh : hit.en;
  }
  DC.collegeLabel = collegeLabel;

  /** "逸夫书院 · 东座 1 栋 · A-301" — the address an account files against. */
  function ownAddress(user) {
    var me = user || DC.me || {};
    return [collegeLabel(me.college), me.block || "", me.dorm || ""]
      .filter(Boolean).join(" · ");
  }
  DC.ownAddress = ownAddress;

  /** One label/value line inside a ticket or account detail body. */
  function row(label, value) {
    return '<div class="tk-row"><span class="lbl2">' + esc(label) + "</span>" +
      "<span>" + value + "</span></div>";
  }
  DC.row = row;

  /** [[2,5],[9,9]] -> "10:00-11:30、15:00-15:30" */
  function pairsText(pairs) {
    if (!pairs || !pairs.length) return t("unitNone");
    return pairs.map(function (p) {
      var a = slotTimeText(p[0]).split("-")[0];
      var b = slotTimeText(p[1]).split("-")[1];
      return a + "-" + b;
    }).join(DC.lang === "zh" ? "、" : ", ");
  }
  DC.pairsText = pairsText;

  /** "Medium" / "High" — capitalise a confidence value for display. */
  function cap(s) {
    s = String(s || "medium").toLowerCase();
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  DC.cap = cap;

  /** A technician's name in the current language. */
  function workerName(w) {
    if (!w) return "";
    return DC.lang === "zh" ? (w.name || w.name_en) : (w.name_en || w.name);
  }
  DC.workerName = workerName;

  /**
   * Five clickable stars, or a read-only verdict when `id` is omitted.
   * With an id the buttons carry data-star/data-id so one delegated listener
   * can serve every ticket on the page.
   */
  function stars(n, id) {
    var out = "";
    for (var i = 1; i <= 5; i++) {
      out += '<button type="button" class="star' + (i <= n ? " on" : "") + '"' +
        (id ? ' data-star="' + i + '" data-id="' + esc(id) + '"' : " disabled") +
        ' aria-label="' + esc(fill(t("fbStar"), { n: i })) + '">★</button>';
    }
    return out;
  }
  DC.stars = stars;

  /** <option> list for the colleges, with `selected` marked. */
  function collegeOptionsHtml(selected) {
    return ((DC.cfg && DC.cfg.colleges) || []).map(function (c) {
      return '<option value="' + esc(c.id) + '"' +
        (c.id === selected ? " selected" : "") + ">" +
        esc(DC.lang === "zh" ? c.zh : c.en) + "</option>";
    }).join("");
  }
  DC.collegeOptionsHtml = collegeOptionsHtml;

  /**
   * <option> list for a college's towers.
   *
   * A college whose only recorded "block" is a catch-all placeholder gets a
   * single disabled entry: a dropdown of one meaningless option is worse than
   * none, but silently dropping the control hides the fact that the tower is
   * written into the room number instead.
   */
  function blockOptionsHtml(collegeId, selected) {
    var college = ((DC.cfg && DC.cfg.colleges) || []).filter(function (c) {
      return c.id === collegeId;
    })[0];
    var blocks = (college && college.blocks) || [];
    var real = blocks.filter(function (b) { return b.indexOf("以现场标识为准") < 0; });
    return '<option value="">' + esc(t("unitNone")) + "</option>" +
      real.map(function (b) {
        return '<option value="' + esc(b) + '"' +
          (b === selected ? " selected" : "") + ">" + esc(b) + "</option>";
      }).join("");
  }
  DC.blockOptionsHtml = blockOptionsHtml;

  /** How many towers a college actually records, ignoring placeholders. */
  function realBlockCount(collegeId) {
    var college = ((DC.cfg && DC.cfg.colleges) || []).filter(function (c) {
      return c.id === collegeId;
    })[0];
    return ((college && college.blocks) || []).filter(function (b) {
      return b.indexOf("以现场标识为准") < 0;
    }).length;
  }
  DC.realBlockCount = realBlockCount;

  /** The "these towers, and here's what they are" hint under a block picker. */
  function blockHintText(collegeId) {
    var college = ((DC.cfg && DC.cfg.colleges) || []).filter(function (c) {
      return c.id === collegeId;
    })[0];
    var txt = college
      ? (DC.lang === "zh" ? college.blocks_zh : college.blocks_en)
      : "";
    if (realBlockCount(collegeId)) return txt || "";
    return txt ? txt + " · " + t("blkAuto") : t("blkAuto");
  }
  DC.blockHintText = blockHintText;

  /**
   * Render the identity bar (role, subtitle, chip) and, when the page declares
   * a `data-page-title`, the breadcrumb back to the menu.
   *
   * Every detail page shares this chrome, so it is written once here rather
   * than copied into eleven HTML files that would drift.
   */
  function applyRoleBar(me) {
    var bar = document.querySelector("[data-rolebar]");
    if (!bar) return;
    var role = (me || {}).role || "";
    var note = bar.querySelector("[data-rolebar-note]");
    if (note) {
      var key = note.getAttribute("data-i18n");
      note.textContent = key ? t(key) : "";
    }
    renderUser();
  }
  DC.applyRoleBar = applyRoleBar;

  /* ====================================================================== */
  /*  chrome                                                                */
  /* ====================================================================== */

  function applyChrome() {
    document.documentElement.lang = L.htmlLang;
    var titleNode = document.querySelector("title[data-i18n]");
    if (titleNode) document.title = t(titleNode.getAttribute("data-i18n"));

    var n, nodes;
    nodes = document.querySelectorAll("[data-i18n]");
    for (n = 0; n < nodes.length; n++) {
      nodes[n].textContent = t(nodes[n].getAttribute("data-i18n"));
    }
    nodes = document.querySelectorAll("[data-i18n-html]");
    for (n = 0; n < nodes.length; n++) {
      nodes[n].innerHTML = t(nodes[n].getAttribute("data-i18n-html"));
    }
    nodes = document.querySelectorAll("[data-i18n-ph]");
    for (n = 0; n < nodes.length; n++) {
      nodes[n].placeholder = t(nodes[n].getAttribute("data-i18n-ph"));
    }
    nodes = document.querySelectorAll("[data-i18n-aria]");
    for (n = 0; n < nodes.length; n++) {
      nodes[n].setAttribute("aria-label", t(nodes[n].getAttribute("data-i18n-aria")));
    }

    var langBox = document.getElementById("lang");
    if (langBox) {
      var btns = langBox.querySelectorAll("button[data-lang]");
      for (n = 0; n < btns.length; n++) {
        btns[n].setAttribute("aria-pressed",
          String(btns[n].getAttribute("data-lang") === LANG));
      }
    }

    ["formMsg", "planMsg", "setupMsg"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el && el.dataset.code) el.textContent = t(el.dataset.code);
    });

    renderUser();
    for (n = 0; n < langHooks.length; n++) {
      try { langHooks[n](LANG); } catch (e) { /* keep the other hooks alive */ }
    }
  }

  function setLang(next) {
    LANG = (next === "en") ? "en" : "zh";
    L = I18N[LANG];
    try { localStorage.setItem(LANG_KEY, LANG); } catch (e) { /* ignore */ }
    applyChrome();
  }
  DC.setLang = setLang;

  /** Register a repaint callback that fires on every language change. */
  function afterLang(fn) { langHooks.push(fn); }
  DC.afterLang = afterLang;

  /* ====================================================================== */
  /*  setup panel (shared: either page can enrol a key)                     */
  /* ====================================================================== */

  function wireSetup() {
    var form = document.getElementById("setupForm");
    if (!form) return;
    var keyInput = document.getElementById("setupKey");
    var btn = document.getElementById("setupBtn");
    var msg = document.getElementById("setupMsg");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var v = keyInput.value;
      if (!v) return;
      busy(btn, true);
      msg.textContent = t("setupWorking");
      post("/api/setup", { key: v }).then(function (res) {
        keyInput.value = "";
        busy(btn, false);
        var code = (res.data && res.data.code) || "";
        if (res.ok && res.data && res.data.ok) {
          setMsg(msg, code || "setup_ok", "ok");
          refreshHealth();
        } else {
          setMsg(msg, code || "err_generic", "err");
          if (!code) msg.textContent = errorText(res.data);
        }
      }).catch(function () {
        keyInput.value = "";
        busy(btn, false);
        setMsg(msg, "setupNetErr", "err");
      });
    });
  }

  /* ====================================================================== */
  /*  boot                                                                  */
  /* ====================================================================== */

  function boot() {
    var langBox = document.getElementById("lang");
    if (langBox) {
      langBox.addEventListener("click", function (e) {
        var b = e.target.closest("button[data-lang]");
        if (b) setLang(b.getAttribute("data-lang"));
      });
    }
    wireSetup();
    setLang(pickLang());
  }

  /**
   * Called once by the role module when its own listeners are in place.
   * A role module registers its repaint hooks via DC.afterLang() *before*
   * calling this, so the very first paint already includes their content.
   *
   * `role` is optional. When given, identity is settled BEFORE the page boots,
   * so a visitor who does not belong here is redirected instead of seeing an
   * empty shell of somebody else's console.
   */
  function start(onReady, role) {
    return Promise.resolve(role ? requireRole(role) : loadMe())
      .then(function (user) {
        if (role && !user) return null;        // a redirect is already under way
        boot();
        return Promise.resolve(onReady ? onReady(user) : null);
      })
      .then(refreshHealth);
  }
  DC.start = start;

  return DC;
})();
