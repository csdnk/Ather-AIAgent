# Operate：从对象入队到执行后重新对账

[返回流程总入口](README.md)

本章把 Go controller-runtime 的运行链与 P3 的业务动作分开。框架代码负责“何时再检查”，具体业务 Reconciler 负责“依据当前事实应该做什么”。P3 参考这种机制，在 Python 中实现业务控制循环；不会直接运行这些 Go 片段来完成记忆调度。

## 先看这一条执行路径

```text
装配 New → 注册 Watch → Start → 启动事件源/队列 → Worker 循环
一次处理 processNextWorkItem → reconcileHandler 前半段（建立 ID）
  → Controller.Reconcile（panic/截止时间包装）
  → 注入的 Do.Reconcile（领域逻辑）
      可选对象适配：按 Request 读当前对象 → 领域 Reconcile
  ← Result / error
reconcileHandler 后半段 → 普通错误退避 / 延迟重查 / 完成清退避
Worker Done → 继续领取下一个对象
关闭：ctx.Done → 队列停止 → 等待 Worker 退出
```

贯穿示例：记忆 m17 达到缓存策略条件；执行动作 a9 已成功但响应丢失。P3 的业务 Reconciler 把 a9 留为 Unknown，并在下一次检查时查询同一个 a9。下面上游代码只提供重新调用的机制，Unknown、a9 和模拟/真实执行端协议由 P3 实现。

以下代码从已核对版本逐段摘录；省略导入、英文说明文档及英文整行注释，保留执行语句、字符串与中文研读注释。标为“片段”的代码保留原方法的局部上下文，不能当成独立函数运行。

## 按执行顺序展开

- [步骤 01：装配：把业务处理器注入公共控制器](#s01)
- [步骤 02：注册哪些事件会触发检查](#s02)
- [步骤 03：启动：建立 Worker 生命周期](#s03)
- [步骤 04：展开：准备队列、启动来源并等缓存可用](#s04)
- [步骤 05：Worker 领取一个待检查对象](#s05)
- [步骤 06：为本次检查建立日志关联](#s06)
- [步骤 07：展开：把关联 ID 传进后续调用](#s07)
- [步骤 08：包装业务：处理 panic 和截止时间](#s08)
- [步骤 09：可选展开：按键重新读取当前对象](#s09)
- [步骤 10：业务返回后：决定重试、稍后再查或完成](#s10)
- [步骤 11：展开：TerminalError 只是错误分类](#s11)
- [步骤 12：展开：为什么 errors.Is 能识别终止错误](#s12)
- [步骤 13：展开：普通队列如何解释重新入队选项](#s13)
- [步骤 14：下游怎样读出本次检查 ID](#s14)

<a id="s01"></a>

### 01　装配：把业务处理器注入公共控制器

**当前执行位置：** `New`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 193—208 行。

**收到什么：** Options 中的 Do、并发数、队列工厂、日志与超时设置。

**这一段怎么处理：** 复制配置构建 Controller。

```go
func New[request comparable](options Options[request]) *Controller[request] {
	// 【中文研读】处理流程：按 Options 构造 Controller；复制处理器、队列工厂、并发、日志及超时配置，此时尚未启动 Worker。
	return &Controller[request]{
		Do:                      options.Do,
		RateLimiter:             options.RateLimiter,
		NewQueue:                options.NewQueue,
		MaxConcurrentReconciles: options.MaxConcurrentReconciles,
		CacheSyncTimeout:        options.CacheSyncTimeout,
		Name:                    options.Name,
		LogConstructor:          options.LogConstructor,
		RecoverPanic:            options.RecoverPanic,
		LeaderElected:           options.LeaderElected,
		EnableWarmup:            options.EnableWarmup,
		ReconciliationTimeout:   options.ReconciliationTimeout,
	}
}
```

**执行后得到什么：** 一个未启动的控制器。

**接下来到哪里：** Watch 注册来源，再调用 Start。

**失败与 P3 责任：** 工厂不连接执行端，不产生业务动作。

<a id="s02"></a>

### 02　注册哪些事件会触发检查

**当前执行位置：** `Controller.Watch`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 262—278 行。

**收到什么：** source。

**这一段怎么处理：** 持锁判断事件源是否已启动；没启动就存入列表，启动后则立即接入队列。

```go
func (c *Controller[request]) Watch(src source.TypedSource[request]) error {
	// 【中文研读】处理流程：注册事件源；加锁后判断是否已启动，未启动则暂存，已启动则立即连接当前上下文和队列。
	// 【中文研读】加锁保护启动/注册等共享状态；不把这把进程内锁理解为多实例分布式锁。
	c.mu.Lock()
	defer c.mu.Unlock()

	// 【中文研读】事件源尚未启动则暂存注册信息，Start/Warmup 时统一启动。
	if !c.startedEventSourcesAndQueue {
		c.startWatches = append(c.startWatches, src)
		return nil
	}

	c.LogConstructor(nil).Info("Starting EventSource", "source", src)
	return src.Start(c.ctx, c.Queue)
}
```

**执行后得到什么：** 一条可在启动后工作的监听来源。

**接下来到哪里：** 进入 Start。

**失败与 P3 责任：** 事件应触发重新读取，不能把旧事件负载直接作为现状。

<a id="s03"></a>

### 03　启动：建立 Worker 生命周期

**当前执行位置：** `Controller.Start`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 310—374 行。

**收到什么：** 控制器生命周期 context。

**这一段怎么处理：** 防重复启动，先准备队列与来源，再启动固定数量 Worker；取消时等待退出。

```go
func (c *Controller[request]) Start(ctx context.Context) error {
	// 【中文研读】处理流程：控制器运行入口；拒绝重复启动，准备队列和事件源，启动固定数量 Worker，收到取消后等待工作者退出。
	// 【中文研读】加锁保护启动/注册等共享状态；不把这把进程内锁理解为多实例分布式锁。
	c.mu.Lock()
	// 【中文研读】防止重复启动一组 Worker 和重复管理同一队列；错误直接返回给上层。
	if c.Started {
		c.mu.Unlock()
		return errors.New("controller was started more than once. This is likely to be caused by being added to a manager multiple times")
	}

	c.initMetrics()

	c.ctx = ctx

	wg := &sync.WaitGroup{}
	err := func() error {
		defer c.mu.Unlock()

		defer utilruntime.HandleCrashWithLogger(c.LogConstructor(nil))

		// 【中文研读】先启动来源并等待同步，再运行工作者，避免缓存尚未准备好就开始业务判断。
		if err := c.startEventSourcesAndQueueLocked(ctx); err != nil {
			return err
		}

		c.LogConstructor(nil).Info("Starting Controller")

		c.LogConstructor(nil).Info("Starting workers", "worker count", c.MaxConcurrentReconciles)
		// 【中文研读】WaitGroup 跟踪将要启动的工作者，关闭时等待在途执行结束。
		wg.Add(c.MaxConcurrentReconciles)
		// 【中文研读】启动固定数量 goroutine，每个不断出队处理；不是每条事件无上限创建一个工作者。
		for i := 0; i < c.MaxConcurrentReconciles; i++ {
			go func() {
				defer wg.Done()
				// 【中文研读】持续处理直到队列关闭返回 false；同一业务对象是否可安全重复执行仍由领域协议保证。
				for c.processNextWorkItem(ctx) {
				}
			}()
		}

		c.Started = true
		return nil
	}()
	if err != nil {
		return err
	}

	// 【中文研读】等待生命周期取消信号；队列关闭和工作者退出配合完成停机，内存待办不等于持久恢复记录。
	<-ctx.Done()
	c.LogConstructor(nil).Info("Shutdown signal received, waiting for all workers to finish")
	// 【中文研读】等在途工作者退出；无视取消的业务处理可能拖延停机，因此外部调用需要明确超时。
	wg.Wait()
	c.LogConstructor(nil).Info("All workers finished")
	return nil
}
```

**执行后得到什么：** Worker 持续执行 processNextWorkItem。

**接下来到哪里：** 首次先进入步骤 04 的准备函数，准备完成后进入步骤 05。

**失败与 P3 责任：** 无视取消的业务处理可能拖住退出；容器重启不能替代业务恢复。

<a id="s04"></a>

### 04　展开：准备队列、启动来源并等缓存可用

**当前执行位置：** `Controller.startEventSourcesAndQueueLocked`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 379—474 行。

**收到什么：** 尚未初始化的队列和注册来源。

**这一段怎么处理：** 仅初始化一次；并行启动各来源，按截止时间等待同步；汇总失败。

```go
func (c *Controller[request]) startEventSourcesAndQueueLocked(ctx context.Context) error {
	// 【中文研读】处理流程：在锁保护下仅初始化一次队列及事件源；并行启动来源，限制缓存同步等待时间，失败返回启动错误。
	var retErr error

	// 【中文研读】sync.Once 避免重复初始化队列和事件源；这是当前进程生命周期内的保护。
	c.didStartEventSourcesOnce.Do(func() {
		queue := c.NewQueue(c.Name, c.RateLimiter)
		// 【中文研读】有原生优先队列则直接使用，否则用兼容包装器；包装器返回的优先级是固定值。
		if priorityQueue, isPriorityQueue := queue.(priorityqueue.PriorityQueue[request]); isPriorityQueue {
			c.Queue = priorityQueue
		} else {
			c.Queue = &priorityQueueWrapper[request]{TypedRateLimitingInterface: queue}
		}
		go func() {
			// 【中文研读】等待生命周期取消信号；队列关闭和工作者退出配合完成停机，内存待办不等于持久恢复记录。
			<-ctx.Done()
			// 【中文研读】停止队列继续供工作者领取，并唤醒等待中的 Get；不声称队列里的对象被写入业务数据库。
			c.Queue.ShutDown()
		}()

		errGroup := &errgroup.Group{}
		// 【中文研读】每个来源分别启动与同步；任一启动失败将影响控制器启动结果。
		for _, watch := range c.startWatches {
			log := c.LogConstructor(nil)
			_, ok := watch.(interface {
				String() string
			})
			if !ok {
				log = log.WithValues("source", fmt.Sprintf("%T", watch))
			} else {
				log = log.WithValues("source", fmt.Sprintf("%s", watch))
			}
			didStartSyncingSource := &atomic.Bool{}
			errGroup.Go(func() error {
				// 【中文研读】为单个事件源的启动同步设置截止时间，防止某个来源拖住全部启动。
				sourceStartCtx, cancel := context.WithTimeout(ctx, c.CacheSyncTimeout)
				defer cancel()

				// 【中文研读】容量为 1 的通道让结果即使遇到外层超时也有机会写入，避免仅因无人接收而卡住。
				sourceStartErrChan := make(chan error, 1) // Buffer chan to not leak goroutine if we time out
				go func() {
					defer close(sourceStartErrChan)
					log.Info("Starting EventSource")

					if err := watch.Start(ctx, c.Queue); err != nil {
						sourceStartErrChan <- err
						return
					}
					// 【中文研读】只有实现同步接口的来源才等待缓存准备；其他来源启动后即可返回。
					syncingSource, ok := watch.(source.TypedSyncingSource[request])
					if !ok {
						return
					}
					didStartSyncingSource.Store(true)
					// 【中文研读】等待缓存达到可用状态，失败交由启动流程上报；缓存同步不等于业务对象全局瞬时最新。
					if err := syncingSource.WaitForSync(sourceStartCtx); err != nil {
						err := fmt.Errorf("failed to wait for %s caches to sync %v: %w", c.Name, syncingSource, err)
						log.Error(err, "Could not wait for Cache to sync")
						sourceStartErrChan <- err
					}
				}()

				select {
				case err := <-sourceStartErrChan:
					return err
				// 【中文研读】同时处理超时与根上下文取消；已进入同步时等待其结果，以避免竞争下丢失真实原因。
				case <-sourceStartCtx.Done():
					if didStartSyncingSource.Load() { // We are racing with WaitForSync, wait for it to let it tell us what happened
						return <-sourceStartErrChan
					}
					if ctx.Err() != nil { // Don't return an error if the root context got cancelled
						return nil
					}
					return fmt.Errorf("timed out waiting for source %s to Start. Please ensure that its Start() method is non-blocking", watch)
				}
			})
		}
		// 【中文研读】汇总并行来源启动结果；有错误时返回，不能悄悄继续宣称所有来源可用。
		retErr = errGroup.Wait()

		// 【中文研读】释放暂存来源引用，避免继续持有其缓存；后续 Watch 将即时启动来源。
		c.startWatches = nil

		c.startedEventSourcesAndQueue = true
	})

	return retErr
}
```

**执行后得到什么：** 已准备队列和来源，或启动错误。

**接下来到哪里：** 返回 Start，开始 Worker 循环。

**失败与 P3 责任：** 这是启动检查，不代表所有业务事实已恢复；缓存同步也不是强一致事务。

<a id="s05"></a>

### 05　Worker 领取一个待检查对象

**当前执行位置：** `Controller.processNextWorkItem`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 479—502 行。

**收到什么：** 队列中的对象键与优先级。

**这一段怎么处理：** GetWithPriority 阻塞取项；关闭时结束；defer Done 保证本次处理收尾。

```go
func (c *Controller[request]) processNextWorkItem(ctx context.Context) bool {
	// 【中文研读】处理流程：从队列取一个对象并处理；关闭时结束 Worker，其他情况确保退出时 Done，再调用 reconcileHandler。
	// 【中文研读】一次取出对象键、优先级和队列关闭状态；键不是某条事件完整快照。
	obj, priority, shutdown := c.Queue.GetWithPriority()
	if shutdown {
		return false
	}

	// 【中文研读】本次出队处理结束必须通知队列；Done 释放处理状态，Forget 清理退避历史，两者用途不同。
	defer c.Queue.Done(obj)

	ctrlmetrics.ActiveWorkers.WithLabelValues(c.Name).Add(1)
	defer ctrlmetrics.ActiveWorkers.WithLabelValues(c.Name).Add(-1)

	c.reconcileHandler(ctx, obj, priority)
	return true
}
```

**执行后得到什么：** 交给 reconcileHandler 的对象键。

**接下来到哪里：** 步骤 06 建立本次执行关联。

**失败与 P3 责任：** Done 仅清除队列处理中标志，不是提交 Action 成功。

<a id="s06"></a>

### 06　为本次检查建立日志关联

**当前执行位置：** `Controller.reconcileHandler`，方法内部片段。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 527—547 行。

**收到什么：** 对象键、优先级、父 context。

**这一段怎么处理：** 创建本次 reconcileID，将它加入日志和 context，然后准备调用业务包装器。

```go
func (c *Controller[request]) reconcileHandler(ctx context.Context, req request, priority int) {
	// 【中文研读】处理流程：为一次对象检查建立日志 ID，执行 Reconcile，然后按错误、指定延迟、旧式重试或成功处理队列。
	reconcileStartTS := time.Now()
	// 【中文研读】defer 在当前函数退出时执行：这里分别用于异常捕获或耗时收尾；不代表可跨进程执行的持久补偿。
	defer func() {
		c.updateMetrics(time.Since(reconcileStartTS))
	}()

	log := c.LogConstructor(&req)
	// 【中文研读】每次检查创建新的执行关联 ID，便于日志定位；稳定 action_id 应另行保存在业务状态中。
	reconcileID := uuid.NewUUID()

	log = log.WithValues("reconcileID", reconcileID)
	ctx = logf.IntoContext(ctx, log)
	// 【中文研读】把同一关联传给内部调用，形成一次检查的日志链；不将它当作业务权限凭证。
	ctx = addReconcileID(ctx, reconcileID)

	log.V(5).Info("Reconciling")
```

**执行后得到什么：** 一次检查的关联上下文。

**接下来到哪里：** 步骤 07 展开 context 写入，随后步骤 08 调业务。

**失败与 P3 责任：** 每次检查的 ID 会改变；P3 原动作 action_id 不能因此换号。

<a id="s07"></a>

### 07　展开：把关联 ID 传进后续调用

**当前执行位置：** `addReconcileID`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 626—629 行。

**收到什么：** 原 context 与本次执行 ID。

**这一段怎么处理：** 使用私有键类型创建派生 context。

```go
func addReconcileID(ctx context.Context, reconcileID types.UID) context.Context {
	// 【中文研读】处理流程：用私有键类型把关联 ID 放入新的 context，避免字符串键与其他组件冲突。
	return context.WithValue(ctx, reconcileIDKey{}, reconcileID)
}
```

**执行后得到什么：** 下游可读取同一个关联 ID。

**接下来到哪里：** 回到 reconcileHandler，进入 Controller.Reconcile。

**失败与 P3 责任：** 只是上下文传播；没有自动创建跨服务 trace，也没有持久保存任务。

<a id="s08"></a>

### 08　包装业务：处理 panic 和截止时间

**当前执行位置：** `Controller.Reconcile`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 212—258 行。

**收到什么：** 对象键及关联 context。

**这一段怎么处理：** 设置 panic 处理与可选超时，调用注入的 c.Do.Reconcile，再区分超时来源。

```go
func (c *Controller[request]) Reconcile(ctx context.Context, req request) (_ reconcile.Result, err error) {
	// 【中文研读】处理流程：包装业务控制循环；先注册 panic 处理，再按配置建立超时上下文，调用领域 Reconcile，最后识别自身超时来源并返回结果。
	// 【中文研读】defer 在当前函数退出时执行：这里分别用于异常捕获或耗时收尾；不代表可跨进程执行的持久补偿。
	defer func() {
		// 【中文研读】捕获业务 panic；根据配置转为普通错误，或记录后再次 panic。
		if r := recover(); r != nil {
			ctrlmetrics.ReconcilePanics.WithLabelValues(c.Name).Inc()

			// 【中文研读】默认恢复 panic，使错误进入后续控制器重试判断；不会撤销已经完成的外部副作用。
			if c.RecoverPanic == nil || *c.RecoverPanic {
				for _, fn := range utilruntime.PanicHandlers {
					fn(ctx, r)
				}
				err = fmt.Errorf("panic: %v [recovered]", r)
				return
			}

			log := logf.FromContext(ctx)
			log.Info(fmt.Sprintf("Observed a panic in reconciler: %v", r))
			panic(r)
		}
	}()

	var timeoutCause error
	// 【中文研读】创建带专属原因的超时上下文，以区分自身限时与上层取消；不是硬杀死 goroutine。
	if c.ReconciliationTimeout > 0 {
		timeoutCause = errReconciliationTimeout
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeoutCause(ctx, c.ReconciliationTimeout, timeoutCause)
		defer cancel()
	}

	// 【中文研读】真正调用业务逻辑；如果实现无视 context，此调用仍可能持续阻塞。
	res, err := c.Do.Reconcile(ctx, req)

	// 【中文研读】只统计由此包装器触发的 DeadlineExceeded；父上下文取消不能误记为本层超时。
	if timeoutCause != nil && ctx.Err() == context.DeadlineExceeded && errors.Is(context.Cause(ctx), timeoutCause) {
		ctrlmetrics.ReconcileTimeouts.WithLabelValues(c.Name).Inc()
	}

	return res, err
}
```

**执行后得到什么：** 领域 Result 与 error。

**接下来到哪里：** 具体业务在 Do.Reconcile 边界；步骤 09 展示可选的对象读取适配。

**失败与 P3 责任：** context 取消是协作式的；外部动作可能已成功，超时后需要查证。

<a id="s09"></a>

### 09　可选展开：按键重新读取当前对象

**当前执行位置：** `objectReconcilerAdapter.Reconcile`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/reconcile/reconcile.go)，注释版第 180—192 行。

**收到什么：** Request 的对象键。

**这一段怎么处理：** 创建接收对象，client.Get 读取；对象不存在可忽略；其余错误传播；然后调用领域 Reconcile。

```go
func (a *objectReconcilerAdapter[object]) Reconcile(ctx context.Context, req Request) (Result, error) {
	// 【中文研读】处理流程：TypedFunc 包装时直接调用函数；objectReconcilerAdapter 包装时先读取当前对象，忽略对象已不存在的错误，再交给领域 Reconciler。具体分支见各实现内部。
	// 【中文研读】通过反射创建泛型对象的新实例，作为客户端读取的接收容器。
	o := reflect.New(reflect.TypeOf(*new(object)).Elem()).Interface().(object)
	// 【中文研读】根据本次对象键读取当前状态；不是直接相信事件里附带的旧值。读取是否来自缓存取决于 client 配置。
	if err := a.client.Get(ctx, req.NamespacedName, o); err != nil {
		// 【中文研读】对象已经不存在时视为无需继续处理；其他读取错误原样传播，供控制器重试。
		return Result{}, client.IgnoreNotFound(err)
	}

	// 【中文研读】拿到对象后才进入业务差异判断；P3 对应重新读取记忆资格、放置与原动作状态。
	return a.objReconciler.Reconcile(ctx, o)
}
```

**执行后得到什么：** 当前读取对象对应的业务处理结果。

**接下来到哪里：** 返回 Controller.Reconcile，再回到步骤 10。

**失败与 P3 责任：** 这是 objectReconcilerAdapter 的可选接法，New 注入的 Do 不一定使用它；P3 应以自己的数据库和执行端为事实来源。

<a id="s10"></a>

### 10　业务返回后：决定重试、稍后再查或完成

**当前执行位置：** `Controller.reconcileHandler`，方法内部片段。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 548—592 行。

**收到什么：** 刚刚返回的 Result/error。

**这一段怎么处理：** 错误优先：终止错误只记录，普通错误退避；无错误时解释 RequeueAfter、旧 Requeue 或成功。

```go
// 【中文研读】经过 panic/超时包装进入领域检查，再统一解释结果。
	result, err := c.Reconcile(ctx, req)
	if result.Priority != nil {
		priority = *result.Priority
	}
	switch {
	// 【中文研读】错误优先于重排时间：普通错误按退避重新入队，终止错误只记录；副作用结果不明时业务应先对账。
	case err != nil:
		// 【中文研读】终止错误不自动重排；代表需要停止自动尝试，不代表成功或自动完成补偿。
		if errors.Is(err, reconcile.TerminalError(nil)) {
			ctrlmetrics.TerminalReconcileErrors.WithLabelValues(c.Name).Inc()
		} else {
			c.Queue.AddWithOpts(priorityqueue.AddOpts{RateLimited: true, Priority: new(priority)}, req)
		}
		ctrlmetrics.ReconcileErrors.WithLabelValues(c.Name).Inc()
		ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelError).Inc()
		if result.RequeueAfter > 0 || result.Requeue { //nolint: staticcheck // We have to handle Requeue until it is removed
			log.Info("Warning: Reconciler returned both a result with either RequeueAfter or Requeue set and a non-nil error. RequeueAfter and Requeue will always be ignored if the error is non-nil. For more details, see: https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/reconcile#Reconciler")
		}
		log.Error(err, "Reconciler error")
	// 【中文研读】无错误且指定延迟：清退避记录后按时间重排；适合等外部状态变化再观察。
	case result.RequeueAfter > 0:
		log.V(5).Info(fmt.Sprintf("Reconcile done, requeueing after %s", result.RequeueAfter))
		// 【中文研读】清除该键的限速重试历史；不会禁止未来事件再次把该对象放入队列。
		c.Queue.Forget(req)
		c.Queue.AddWithOpts(priorityqueue.AddOpts{After: result.RequeueAfter, Priority: new(priority)}, req)
		ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelRequeueAfter).Inc()
	// 【中文研读】保留旧 API 的限速重排分支；新设计宜明确等待时间与错误重试的区别。
	case result.Requeue: //nolint: staticcheck // We have to handle it until it is removed
		log.V(5).Info("Reconcile done, requeueing")
		c.Queue.AddWithOpts(priorityqueue.AddOpts{RateLimited: true, Priority: new(priority)}, req)
		ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelRequeue).Inc()
	default:
		log.V(5).Info("Reconcile successful")
		// 【中文研读】清除该键的限速重试历史；不会禁止未来事件再次把该对象放入队列。
		c.Queue.Forget(req)
		ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelSuccess).Inc()
	}
}
```

**执行后得到什么：** 队列中的下一次检查安排。

**接下来到哪里：** 返回 Worker，执行 defer Done，再领下一个对象。

**失败与 P3 责任：** 这里没有 Unknown 自动查询代码；领域必须把 Unknown 转成持久对账待办和下一次检查条件。

<a id="s11"></a>

### 11　展开：TerminalError 只是错误分类

**当前执行位置：** `TerminalError`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/reconcile/reconcile.go)，注释版第 197—201 行。

**收到什么：** 确定不应自动重试的原错误。

**这一段怎么处理：** 包装错误，供 errors.Is 判断。

```go
func TerminalError(wrapped error) error {
	// 【中文研读】处理流程：包装明确不应自动重试的错误；控制器仍会记录错误，但不会按普通错误自动重新入队。
	// 【中文研读】只增加“不自动重试”的错误分类，不执行补偿，也不把失败变成功。
	return &terminalError{err: wrapped}
}
```

**执行后得到什么：** terminalError。

**接下来到哪里：** 错误分类会由上一步识别；Is 的实现见下一步。

**失败与 P3 责任：** 不会自动补偿、不代表业务成功；人工处理须读取业务失败原因。

<a id="s12"></a>

### 12　展开：为什么 errors.Is 能识别终止错误

**当前执行位置：** `terminalError.Is`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/reconcile/reconcile.go)，注释版第 224—228 行。

**收到什么：** 要比较的目标 error。

**这一段怎么处理：** 按终止错误类型判断，支持错误链识别。

```go
func (te *terminalError) Is(target error) bool {
	// 【中文研读】处理流程：支持 errors.Is 判断目标是否具有 terminalError 类型；比较的是错误类别，不是错误字符串。
	tp := &terminalError{}
	return errors.As(target, &tp)
}
```

**执行后得到什么：** 布尔结果。

**接下来到哪里：** 回到错误分支。

**失败与 P3 责任：** 不能按字符串包含“失败”来替代明确错误类型。

<a id="s13"></a>

### 13　展开：普通队列如何解释重新入队选项

**当前执行位置：** `priorityQueueWrapper.AddWithOpts`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 636—649 行。

**收到什么：** RateLimited、After 及待入队对象。

**这一段怎么处理：** 按退避优先、延迟其次、立即最后的规则转发到底层队列。

```go
func (p *priorityQueueWrapper[request]) AddWithOpts(opts priorityqueue.AddOpts, items ...request) {
	// 【中文研读】处理流程：把通用队列适配为带选项接口；逐项按限速、延后或立即入队分派，不提供真正的优先级排序。
	for _, item := range items {
		switch {
		// 【中文研读】普通队列兼容层优先使用退避入队；其后才考虑延迟，最后才立即入队。
		case opts.RateLimited:
			p.TypedRateLimitingInterface.AddRateLimited(item)
		case opts.After > 0:
			p.TypedRateLimitingInterface.AddAfter(item, opts.After)
		default:
			p.TypedRateLimitingInterface.Add(item)
		}
	}
}
```

**执行后得到什么：** 重新安排的对象键。

**接下来到哪里：** 未来 Worker 再次读取持久现状。

**失败与 P3 责任：** 这只是兼容包装；不会给普通队列补出真正的优先级排序或磁盘持久性。

<a id="s14"></a>

### 14　下游怎样读出本次检查 ID

**当前执行位置：** `ReconcileIDFromContext`，完整方法或类型摘录。[出处](../第三方源码中文注释/controller-runtime/pkg/internal/controller/controller.go)，注释版第 610—618 行。

**收到什么：** 内部调用收到的 context。

**这一段怎么处理：** 按私有键取值并检查类型，没有则返回空值。

```go
func ReconcileIDFromContext(ctx context.Context) types.UID {
	// 【中文研读】处理流程：从上下文读取本次 reconcileID；未设置或类型不符时返回空字符串。
	r, ok := ctx.Value(reconcileIDKey{}).(types.UID)
	if !ok {
		return ""
	}

	return r
}
```

**执行后得到什么：** 可写入日志的本次 reconcileID。

**接下来到哪里：** P3 对应在各处理节点读取自己的 Request/Task/TraceContext。

**失败与 P3 责任：** P3 应分别设计 trace_id、task_id、attempt_id、action_id，不能把一个临时 ID 混用为全部身份。

## 本章出现的外部调用与停止展开的位置

| 调用或能力 | 在这条链中的作用 | 为什么在这里画边界 |
|---|---|---|
| source.Start / WaitForSync | 启动事件监听并等待可同步来源准备 | 具体事件源和 Kubernetes 缓存实现不在本次摘录范围 |
| Queue.Get / Done / Forget / AddWithOpts | 内存排队、处理标记、退避与延迟 | 队列接口不是持久 Task 表；重建依据在持久对象/业务数据库 |
| c.Do.Reconcile / objReconciler.Reconcile | 读取事实、生成动作、查询结果 | 这里是必须由领域实现的接口，不存在可复制的通用分层业务算法 |
| client.Get | 按对象键读取当前对象 | 可经过缓存，读到的不是保证全局瞬时最新状态 |
| log / context / metrics | 一次执行的关联、取消与观测 | reconcileID 不自动等于 W3C trace_id；框架指标不纳入本轮平台建设 |

## 对照 P3 应怎样使用

P3 需要持久保存 action_id、期望状态、观察状态、执行证据和 Unknown。重试时先读这些事实：已完成就复用结果，未知就查询原动作，明确失败再按领域规则决定是否重试。队列中相同 key 被合并，不代表外部副作用只发生一次。

trace_id 用于跨入口/任务/外部调用关联；每次 attempt/span 另有标识；action_id 跨重试保持稳定。恢复依据是持久业务记录与执行端观察，日志帮助定位，不能让采样日志成为唯一恢复依据。

普通错误、延迟重查、TerminalError 和成功各有不同队列行为。删除清理、容量预算、未知动作对账仍由 Operate 定义，不能只把 RequeueAfter 设好就称为调度闭环。
