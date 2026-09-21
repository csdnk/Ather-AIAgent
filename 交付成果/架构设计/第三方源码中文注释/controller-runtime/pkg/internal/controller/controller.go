/*
Copyright 2018 The Kubernetes Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// 【中文研读】阅读主线：Start 启动事件源和工作队列 → Worker 出队 → reconcileHandler 建立关联 → Reconcile 调业务 → 按错误/延时/成功重新排队。队列本身不是 P3 持久任务库。
// 【中文研读】保留原始许可证与代码，仅增加中文说明；P3 参考控制机制，未直接将 Go 库接入 Python 业务。
package controller

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"github.com/go-logr/logr"
	"golang.org/x/sync/errgroup"
	"k8s.io/apimachinery/pkg/types"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/apimachinery/pkg/util/uuid"
	"k8s.io/client-go/util/workqueue"

	"sigs.k8s.io/controller-runtime/pkg/controller/priorityqueue"
	ctrlmetrics "sigs.k8s.io/controller-runtime/pkg/internal/controller/metrics"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	"sigs.k8s.io/controller-runtime/pkg/source"
)

// errReconciliationTimeout is the error used as the cause when the ReconciliationTimeout guardrail fires.
// This allows us to distinguish wrapper timeouts from user-initiated context cancellations.
var errReconciliationTimeout = errors.New("reconciliation timeout")

// Options are the arguments for creating a new Controller.
// 【中文研读】创建参数：业务处理器与公共运行机制分开注入。
type Options[request comparable] struct {
	// Reconciler is a function that can be called at any time with the Name / Namespace of an object and
	// ensures that the state of the system matches the state specified in the object.
	// Defaults to the DefaultReconcileFunc.
	// 【中文研读】业务 Reconciler 接口，真正的期望/观察比较发生在注入实现里。
	Do reconcile.TypedReconciler[request]

	// RateLimiter is used to limit how frequently requests may be queued into the work queue.
	RateLimiter workqueue.TypedRateLimiter[request]

	// NewQueue constructs the queue for this controller once the controller is ready to start.
	// This is a func because the standard Kubernetes work queues start themselves immediately, which
	// leads to goroutine leaks if something calls controller.New repeatedly.
	NewQueue func(controllerName string, rateLimiter workqueue.TypedRateLimiter[request]) workqueue.TypedRateLimitingInterface[request]

	// MaxConcurrentReconciles is the maximum number of concurrent Reconciles which can be run. Defaults to 1.
	// See controller.TypedOptions.MaxConcurrentReconciles for full documentation.
	// 【中文研读】控制并发检查数量；同一个队列键仍由队列避免同时处理，不等于跨进程业务幂等。
	MaxConcurrentReconciles int

	// CacheSyncTimeout refers to the time limit set on waiting for cache to sync
	// Defaults to 2 minutes if not set.
	// 【中文研读】启动与缓存同步的等待上限，避免依赖异常时启动无限悬挂。
	CacheSyncTimeout time.Duration

	// Name is used to uniquely identify a Controller in tracing, logging and monitoring.  Name is required.
	Name string

	// LogConstructor is used to construct a logger to then log messages to users during reconciliation,
	// or for example when a watch is started.
	// Note: LogConstructor has to be able to handle nil requests as we are also using it
	// outside the context of a reconciliation.
	LogConstructor func(request *request) logr.Logger

	// RecoverPanic indicates whether the panic caused by reconcile should be recovered.
	// Defaults to true.
	RecoverPanic *bool

	// LeaderElected indicates whether the controller is leader elected or always running.
	LeaderElected *bool

	// EnableWarmup specifies whether the controller should start its sources
	// when the manager is not the leader.
	// Defaults to false, which means that the controller will wait for leader election to start
	// before starting sources.
	EnableWarmup *bool

	// ReconciliationTimeout is used as the timeout passed to the context of each Reconcile call.
	// By default, there is no timeout.
	// 【中文研读】本次 Reconcile 的 context 截止时间；业务代码必须观察取消，才能及时结束。
	ReconciliationTimeout time.Duration
}

// Controller implements controller.Controller.
// 【中文研读】运行对象：保存队列、事件源、锁、上下文与工作者配置，协调调度，不替领域判断业务完成。
type Controller[request comparable] struct {
	// Name is used to uniquely identify a Controller in tracing, logging and monitoring.  Name is required.
	Name string

	// MaxConcurrentReconciles is the number of worker goroutines spawned to process work queue items.
	// See controller.TypedOptions.MaxConcurrentReconciles for full documentation.
	// 【中文研读】控制并发检查数量；同一个队列键仍由队列避免同时处理，不等于跨进程业务幂等。
	MaxConcurrentReconciles int

	// Reconciler is a function that can be called at any time with the Name / Namespace of an object and
	// ensures that the state of the system matches the state specified in the object.
	// Defaults to the DefaultReconcileFunc.
	// 【中文研读】业务 Reconciler 接口，真正的期望/观察比较发生在注入实现里。
	Do reconcile.TypedReconciler[request]

	// RateLimiter is used to limit how frequently requests may be queued into the work queue.
	RateLimiter workqueue.TypedRateLimiter[request]

	// NewQueue constructs the queue for this controller once the controller is ready to start.
	// This is a func because the standard Kubernetes work queues start themselves immediately, which
	// leads to goroutine leaks if something calls controller.New repeatedly.
	NewQueue func(controllerName string, rateLimiter workqueue.TypedRateLimiter[request]) workqueue.TypedRateLimitingInterface[request]

	// Queue is an listeningQueue that listens for events from Informers and adds object keys to
	// the Queue for processing
	// 【中文研读】事件源把对象键放进队列；持久业务状态应保存在 API 对象或外部数据库中。
	Queue priorityqueue.PriorityQueue[request]

	// mu is used to synchronize Controller setup
	mu sync.Mutex

	// Started is true if the Controller has been Started
	Started bool

	// ctx is the context that was passed to Start() and used when starting watches.
	//
	// According to the docs, contexts should not be stored in a struct: https://golang.org/pkg/context,
	// while we usually always strive to follow best practices, we consider this a legacy case and it should
	// undergo a major refactoring and redesign to allow for context to not be stored in a struct.
	ctx context.Context

	// CacheSyncTimeout refers to the time limit set on waiting for cache to sync
	// Defaults to 2 minutes if not set.
	// 【中文研读】启动与缓存同步的等待上限，避免依赖异常时启动无限悬挂。
	CacheSyncTimeout time.Duration

	// startWatches maintains a list of sources, handlers, and predicates to start when the controller is started.
	startWatches []source.TypedSource[request]

	// startedEventSourcesAndQueue is used to track if the event sources have been started.
	// It ensures that we append sources to c.startWatches only until we call Start() / Warmup()
	// It is true if startEventSourcesAndQueueLocked has been called at least once.
	startedEventSourcesAndQueue bool

	// didStartEventSourcesOnce is used to ensure that the event sources are only started once.
	didStartEventSourcesOnce sync.Once

	// LogConstructor is used to construct a logger to then log messages to users during reconciliation,
	// or for example when a watch is started.
	// Note: LogConstructor has to be able to handle nil requests as we are also using it
	// outside the context of a reconciliation.
	LogConstructor func(request *request) logr.Logger

	// RecoverPanic indicates whether the panic caused by reconcile should be recovered.
	// Defaults to true.
	RecoverPanic *bool

	// LeaderElected indicates whether the controller is leader elected or always running.
	LeaderElected *bool

	// EnableWarmup specifies whether the controller should start its sources when the manager is not
	// the leader. This is useful for cases where sources take a long time to start, as it allows
	// for the controller to warm up its caches even before it is elected as the leader. This
	// improves leadership failover time, as the caches will be prepopulated before the controller
	// transitions to be leader.
	//
	// Setting EnableWarmup to true and NeedLeaderElection to true means the controller will start its
	// sources without waiting to become leader.
	// Setting EnableWarmup to true and NeedLeaderElection to false is a no-op as controllers without
	// leader election do not wait on leader election to start their sources.
	// Defaults to false.
	EnableWarmup *bool

	// 【中文研读】本次 Reconcile 的 context 截止时间；业务代码必须观察取消，才能及时结束。
	ReconciliationTimeout time.Duration
}

// New returns a new Controller configured with the given options.
// 【中文研读】方法职责：按 Options 构造 Controller；复制处理器、队列工厂、并发、日志及超时配置，此时尚未启动 Worker。
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

// Reconcile implements reconcile.Reconciler.
// 【中文研读】方法职责：包装业务控制循环；先注册 panic 处理，再按配置建立超时上下文，调用领域 Reconcile，最后识别自身超时来源并返回结果。
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

	// Check if the reconciliation timed out due to our wrapper timeout guardrail.
	// We check ctx.Err() == context.DeadlineExceeded first to ensure the context was actually
	// cancelled due to a deadline (not parent cancellation or other reasons), then verify it was
	// our specific timeout cause. This prevents false positives from parent context cancellations
	// or other timeout scenarios.
	// 【中文研读】只统计由此包装器触发的 DeadlineExceeded；父上下文取消不能误记为本层超时。
	if timeoutCause != nil && ctx.Err() == context.DeadlineExceeded && errors.Is(context.Cause(ctx), timeoutCause) {
		ctrlmetrics.ReconcileTimeouts.WithLabelValues(c.Name).Inc()
	}

	return res, err
}

// Watch implements controller.Controller.
// 【中文研读】方法职责：注册事件源；加锁后判断是否已启动，未启动则暂存，已启动则立即连接当前上下文和队列。
func (c *Controller[request]) Watch(src source.TypedSource[request]) error {
	// 【中文研读】处理流程：注册事件源；加锁后判断是否已启动，未启动则暂存，已启动则立即连接当前上下文和队列。
	// 【中文研读】加锁保护启动/注册等共享状态；不把这把进程内锁理解为多实例分布式锁。
	c.mu.Lock()
	defer c.mu.Unlock()

	// Sources weren't started yet, store the watches locally and return.
	// These sources are going to be held until either Warmup() or Start(...) is called.
	// 【中文研读】事件源尚未启动则暂存注册信息，Start/Warmup 时统一启动。
	if !c.startedEventSourcesAndQueue {
		c.startWatches = append(c.startWatches, src)
		return nil
	}

	c.LogConstructor(nil).Info("Starting EventSource", "source", src)
	return src.Start(c.ctx, c.Queue)
}

// NeedLeaderElection implements the manager.LeaderElectionRunnable interface.
// 【中文研读】方法职责：读取是否需要 leader 身份才运行；未配置时默认需要，由上层 Manager 决定选主安排。
func (c *Controller[request]) NeedLeaderElection() bool {
	// 【中文研读】处理流程：读取是否需要 leader 身份才运行；未配置时默认需要，由上层 Manager 决定选主安排。
	if c.LeaderElected == nil {
		return true
	}
	return *c.LeaderElected
}

// Warmup implements the manager.WarmupRunnable interface.
// 【中文研读】方法职责：按配置预启动事件源和缓存；关闭时立即返回，开启时在锁内设置上下文并初始化事件源，不启动工作者执行业务。
func (c *Controller[request]) Warmup(ctx context.Context) error {
	// 【中文研读】处理流程：按配置预启动事件源和缓存；关闭时立即返回，开启时在锁内设置上下文并初始化事件源，不启动工作者执行业务。
	if c.EnableWarmup == nil || !*c.EnableWarmup {
		return nil
	}

	// 【中文研读】加锁保护启动/注册等共享状态；不把这把进程内锁理解为多实例分布式锁。
	c.mu.Lock()
	defer c.mu.Unlock()

	// Set the ctx so later calls to watch use this internal context
	c.ctx = ctx

	return c.startEventSourcesAndQueueLocked(ctx)
}

// Start implements controller.Controller.
// 【中文研读】方法职责：控制器运行入口；拒绝重复启动，准备队列和事件源，启动固定数量 Worker，收到取消后等待工作者退出。
func (c *Controller[request]) Start(ctx context.Context) error {
	// 【中文研读】处理流程：控制器运行入口；拒绝重复启动，准备队列和事件源，启动固定数量 Worker，收到取消后等待工作者退出。
	// use an IIFE to get proper lock handling
	// but lock outside to get proper handling of the queue shutdown
	// 【中文研读】加锁保护启动/注册等共享状态；不把这把进程内锁理解为多实例分布式锁。
	c.mu.Lock()
	// 【中文研读】防止重复启动一组 Worker 和重复管理同一队列；错误直接返回给上层。
	if c.Started {
		c.mu.Unlock()
		return errors.New("controller was started more than once. This is likely to be caused by being added to a manager multiple times")
	}

	c.initMetrics()

	// Set the internal context.
	c.ctx = ctx

	wg := &sync.WaitGroup{}
	err := func() error {
		defer c.mu.Unlock()

		// TODO(pwittrock): Reconsider HandleCrash
		defer utilruntime.HandleCrashWithLogger(c.LogConstructor(nil))

		// NB(directxman12): launch the sources *before* trying to wait for the
		// caches to sync so that they have a chance to register their intended
		// caches.
		// 【中文研读】先启动来源并等待同步，再运行工作者，避免缓存尚未准备好就开始业务判断。
		if err := c.startEventSourcesAndQueueLocked(ctx); err != nil {
			return err
		}

		c.LogConstructor(nil).Info("Starting Controller")

		// Launch workers to process resources
		c.LogConstructor(nil).Info("Starting workers", "worker count", c.MaxConcurrentReconciles)
		// 【中文研读】WaitGroup 跟踪将要启动的工作者，关闭时等待在途执行结束。
		wg.Add(c.MaxConcurrentReconciles)
		// 【中文研读】启动固定数量 goroutine，每个不断出队处理；不是每条事件无上限创建一个工作者。
		for i := 0; i < c.MaxConcurrentReconciles; i++ {
			go func() {
				defer wg.Done()
				// Run a worker thread that just dequeues items, processes them, and marks them done.
				// It enforces that the reconcileHandler is never invoked concurrently with the same object.
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

// startEventSourcesAndQueueLocked launches all the sources registered with this controller and waits
// for them to sync. It returns an error if any of the sources fail to start or sync.
// 【中文研读】方法职责：在锁保护下仅初始化一次队列及事件源；并行启动来源，限制缓存同步等待时间，失败返回启动错误。
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
				// Use a timeout for starting and syncing the source to avoid silently
				// blocking startup indefinitely if it doesn't come up.
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

		// All the watches have been started, we can reset the local slice.
		//
		// We should never hold watches more than necessary, each watch source can hold a backing cache,
		// which won't be garbage collected if we hold a reference to it.
		// 【中文研读】释放暂存来源引用，避免继续持有其缓存；后续 Watch 将即时启动来源。
		c.startWatches = nil

		// Mark event sources as started after resetting the startWatches slice so that watches from
		// a new Watch() call are immediately started.
		c.startedEventSourcesAndQueue = true
	})

	return retErr
}

// processNextWorkItem will read a single work item off the workqueue and
// attempt to process it, by calling the reconcileHandler.
// 【中文研读】方法职责：从队列取一个对象并处理；关闭时结束 Worker，其他情况确保退出时 Done，再调用 reconcileHandler。
func (c *Controller[request]) processNextWorkItem(ctx context.Context) bool {
	// 【中文研读】处理流程：从队列取一个对象并处理；关闭时结束 Worker，其他情况确保退出时 Done，再调用 reconcileHandler。
	// 【中文研读】一次取出对象键、优先级和队列关闭状态；键不是某条事件完整快照。
	obj, priority, shutdown := c.Queue.GetWithPriority()
	if shutdown {
		// Stop working
		return false
	}

	// We call Done here so the workqueue knows we have finished
	// processing this item. We also must remember to call Forget if we
	// do not want this work item being re-queued. For example, we do
	// not call Forget if a transient error occurs, instead the item is
	// put back on the workqueue and attempted again after a back-off
	// period.
	// 【中文研读】本次出队处理结束必须通知队列；Done 释放处理状态，Forget 清理退避历史，两者用途不同。
	defer c.Queue.Done(obj)

	ctrlmetrics.ActiveWorkers.WithLabelValues(c.Name).Add(1)
	defer ctrlmetrics.ActiveWorkers.WithLabelValues(c.Name).Add(-1)

	c.reconcileHandler(ctx, obj, priority)
	return true
}

const (
	labelError        = "error"
	labelRequeueAfter = "requeue_after"
	labelRequeue      = "requeue"
	labelSuccess      = "success"
)

// 【中文研读】方法职责：初始化各类指标为零并设置 Worker 数；这是原框架内置观测，P3 当前暂缓指标平台，不照此新增交付范围。
func (c *Controller[request]) initMetrics() {
	// 【中文研读】处理流程：初始化各类指标为零并设置 Worker 数；这是原框架内置观测，P3 当前暂缓指标平台，不照此新增交付范围。
	ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelError).Add(0)
	ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelRequeueAfter).Add(0)
	ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelRequeue).Add(0)
	ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelSuccess).Add(0)
	ctrlmetrics.ReconcileErrors.WithLabelValues(c.Name).Add(0)
	ctrlmetrics.TerminalReconcileErrors.WithLabelValues(c.Name).Add(0)
	ctrlmetrics.ReconcilePanics.WithLabelValues(c.Name).Add(0)
	ctrlmetrics.ReconcileTimeouts.WithLabelValues(c.Name).Add(0)
	ctrlmetrics.WorkerCount.WithLabelValues(c.Name).Set(float64(c.MaxConcurrentReconciles))
	ctrlmetrics.ActiveWorkers.WithLabelValues(c.Name).Set(0)
}

// 【中文研读】方法职责：为一次对象检查建立日志 ID，执行 Reconcile，然后按错误、指定延迟、旧式重试或成功处理队列。
func (c *Controller[request]) reconcileHandler(ctx context.Context, req request, priority int) {
	// 【中文研读】处理流程：为一次对象检查建立日志 ID，执行 Reconcile，然后按错误、指定延迟、旧式重试或成功处理队列。
	// Update metrics after processing each item
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

	// RunInformersAndControllers the syncHandler, passing it the Namespace/Name string of the
	// resource to be synced.
	log.V(5).Info("Reconciling")
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
		// The result.RequeueAfter request will be lost, if it is returned
		// along with a non-nil error. But this is intended as
		// We need to drive to stable reconcile loops before queuing due
		// to result.RequestAfter
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
		// Finally, if no error occurs we Forget this item so it does not
		// get queued again until another change happens.
		// 【中文研读】清除该键的限速重试历史；不会禁止未来事件再次把该对象放入队列。
		c.Queue.Forget(req)
		ctrlmetrics.ReconcileTotal.WithLabelValues(c.Name, labelSuccess).Inc()
	}
}

// GetLogger returns this controller's logger.
// 【中文研读】方法职责：取得控制器级日志实例；传 nil 表示不绑定某一具体对象请求。
func (c *Controller[request]) GetLogger() logr.Logger {
	// 【中文研读】处理流程：取得控制器级日志实例；传 nil 表示不绑定某一具体对象请求。
	return c.LogConstructor(nil)
}

// updateMetrics updates prometheus metrics within the controller.
// 【中文研读】方法职责：记录本次控制循环耗时；观测值不替代业务是否完成的持久证据。
func (c *Controller[request]) updateMetrics(reconcileTime time.Duration) {
	// 【中文研读】处理流程：记录本次控制循环耗时；观测值不替代业务是否完成的持久证据。
	ctrlmetrics.ReconcileTime.WithLabelValues(c.Name).Observe(reconcileTime.Seconds())
}

// ReconcileIDFromContext gets the reconcileID from the current context.
// 【中文研读】方法职责：从上下文读取本次 reconcileID；未设置或类型不符时返回空字符串。
func ReconcileIDFromContext(ctx context.Context) types.UID {
	// 【中文研读】处理流程：从上下文读取本次 reconcileID；未设置或类型不符时返回空字符串。
	r, ok := ctx.Value(reconcileIDKey{}).(types.UID)
	if !ok {
		return ""
	}

	return r
}

// reconcileIDKey is a context.Context Value key. Its associated value should
// be a types.UID.
// 【中文研读】私有空结构类型只用于 context 键隔离，防止不同模块发生同名键冲突。
type reconcileIDKey struct{}

// 【中文研读】方法职责：用私有键类型把关联 ID 放入新的 context，避免字符串键与其他组件冲突。
func addReconcileID(ctx context.Context, reconcileID types.UID) context.Context {
	// 【中文研读】处理流程：用私有键类型把关联 ID 放入新的 context，避免字符串键与其他组件冲突。
	return context.WithValue(ctx, reconcileIDKey{}, reconcileID)
}

type priorityQueueWrapper[request comparable] struct {
	workqueue.TypedRateLimitingInterface[request]
}

// 【中文研读】方法职责：把通用队列适配为带选项接口；逐项按限速、延后或立即入队分派，不提供真正的优先级排序。
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

// 【中文研读】方法职责：普通队列没有优先级；取出对象后返回固定优先级 0，并透传关闭标志。
func (p *priorityQueueWrapper[request]) GetWithPriority() (request, int, bool) {
	// 【中文研读】处理流程：普通队列没有优先级；取出对象后返回固定优先级 0，并透传关闭标志。
	item, shutdown := p.TypedRateLimitingInterface.Get()
	return item, 0, shutdown
}
