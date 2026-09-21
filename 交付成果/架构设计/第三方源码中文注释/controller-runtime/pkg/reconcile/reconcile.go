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

// 【中文研读】阅读主线：Request 只定位对象，Reconcile 重新读取当前状态，Result 告知是否稍后重查，error 区分可重试与终止。它是 Go 接口，不是 Python 工作流引擎。
// 【中文研读】保留原始许可证与代码，仅增加中文说明；P3 参考控制机制，未直接将 Go 库接入 Python 业务。
package reconcile

import (
	"context"
	"errors"
	"reflect"
	"time"

	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// Result contains the result of a Reconciler invocation.
// 【中文研读】结果封套：重查时间、旧式重试标志和队列优先级；不是数据库里的业务 Action 完成证据。
type Result struct {
	// Requeue tells the Controller to perform a ratelimited requeue
	// using the workqueues ratelimiter. Defaults to false.
	//
	// This setting is deprecated as it causes confusion and there is
	// no good reason to use it. When waiting for an external event to
	// happen, either the duration until it is supposed to happen or an
	// appropriate poll interval should be used, rather than an
	// interval emitted by a ratelimiter whose purpose it is to control
	// retry on error.
	//
	// Deprecated: Use `RequeueAfter` instead.
	// 【中文研读】旧式重新入队选项，当前源码已弃用；等待外部状态变化宜明确给 RequeueAfter。
	Requeue bool

	// RequeueAfter if greater than 0, tells the Controller to requeue the reconcile key after the Duration.
	// Implies that Requeue is true, there is no need to set Requeue to true at the same time as RequeueAfter.
	// 【中文研读】无错误时，按这个时长延后重新检查同一对象；不是睡在当前请求里等待。
	RequeueAfter time.Duration

	// Priority is the priority that will be used if the item gets re-enqueued (also if an error is returned).
	// If Priority is not set the original Priority of the request is preserved.
	// Note: Priority is only respected if the controller is using a priorityqueue.PriorityQueue.
	// 【中文研读】可选队列优先级覆盖；只有支持优先队列的控制器才使用该语义。
	Priority *int
}

// IsZero returns true if this result is empty.
// 【中文研读】方法职责：判断是否为空结果；nil 指针也算空，非空时与零值 Result 比较，供调用方判断没有重新调度要求。
func (r *Result) IsZero() bool {
	// 【中文研读】处理流程：判断是否为空结果；nil 指针也算空，非空时与零值 Result 比较，供调用方判断没有重新调度要求。
	if r == nil {
		return true
	}
	return *r == Result{}
}

// Request contains the information necessary to reconcile a Kubernetes object.  This includes the
// information to uniquely identify the object - its Name and Namespace.  It does NOT contain information about
// any specific Event or the object contents itself.
// 【中文研读】请求只带对象名称与 Namespace；不会保存触发事件的全部旧对象快照。
type Request struct {
	// NamespacedName is the name and namespace of the object to reconcile.
	types.NamespacedName
}

/*
Reconciler implements a Kubernetes API for a specific Resource by Creating, Updating or Deleting Kubernetes
objects, or by making changes to systems external to the cluster (e.g. cloudproviders, github, etc).

reconcile implementations compare the state specified in an object by a user against the actual cluster state,
and then perform operations to make the actual cluster state reflect the state specified by the user.

Typically, reconcile is triggered by a Controller in response to cluster Events (e.g. Creating, Updating,
Deleting Kubernetes objects) or external Events (GitHub Webhooks, polling external sources, etc).

Example reconcile Logic:

* Read an object and all the Pods it owns.
* Observe that the object spec specifies 5 replicas but actual cluster contains only 1 Pod replica.
* Create 4 Pods and set their OwnerReferences to the object.

reconcile may be implemented as either a type:

	type reconciler struct {}

	func (reconciler) Reconcile(ctx context.Context, o reconcile.Request) (reconcile.Result, error) {
		// Implement business logic of reading and writing objects here
		return reconcile.Result{}, nil
	}

Or as a function:

	reconcile.Func(func(ctx context.Context, o reconcile.Request) (reconcile.Result, error) {
		// Implement business logic of reading and writing objects here
		return reconcile.Result{}, nil
	})

Reconciliation is level-based, meaning action isn't driven off changes in individual Events, but instead is
driven by actual cluster state read from the apiserver or a local cache.
For example if responding to a Pod Delete Event, the Request won't contain that a Pod was deleted,
instead the reconcile function observes this when reading the cluster state and seeing the Pod as missing.
*/
type Reconciler = TypedReconciler[Request]

// TypedReconciler implements an API for a specific Resource by Creating, Updating or Deleting Kubernetes
// objects, or by making changes to systems external to the cluster (e.g. cloudproviders, github, etc).
//
// The request type is what event handlers put into the workqueue. The workqueue then de-duplicates identical
// requests.
// 【中文研读】接口契约：读取当前状态并计算差异；重复触发应能安全重做观察，业务副作用仍需幂等。
type TypedReconciler[request comparable] interface {
	// Reconcile performs a full reconciliation for the object referred to by the Request.
	//
	// If the returned error is non-nil, the Result is ignored and the request will be
	// requeued using exponential backoff. The only exception is if the error is a
	// TerminalError in which case no requeuing happens.
	//
	// If the error is nil and the returned Result has a non-zero result.RequeueAfter, the request
	// will be requeued after the specified duration.
	//
	// If the error is nil and result.RequeueAfter is zero and result.Requeue is true, the request
	// will be requeued using exponential backoff.
	// 【中文研读】输入为取消上下文和对象定位键；普通 error 触发退避重试，TerminalError 不自动重试，无错误时解释 Result。
	Reconcile(context.Context, request) (Result, error)
}

// Func is a function that implements the reconcile interface.
type Func = TypedFunc[Request]

// TypedFunc is a function that implements the reconcile interface.
type TypedFunc[request comparable] func(context.Context, request) (Result, error)

var _ Reconciler = Func(nil)

// Reconcile implements Reconciler.
// 【中文研读】方法职责：TypedFunc 包装时直接调用函数；objectReconcilerAdapter 包装时先读取当前对象，忽略对象已不存在的错误，再交给领域 Reconciler。具体分支见各实现内部。
func (r TypedFunc[request]) Reconcile(ctx context.Context, req request) (Result, error) {
	// 【中文研读】处理流程：TypedFunc 包装时直接调用函数；objectReconcilerAdapter 包装时先读取当前对象，忽略对象已不存在的错误，再交给领域 Reconciler。具体分支见各实现内部。
	// 【中文研读】函数适配没有增加存储与恢复逻辑，只让普通函数满足统一接口。
	return r(ctx, req)
}

// ObjectReconciler is a specialized version of Reconciler that acts on instances of client.Object. Each reconciliation
// event gets the associated object from Kubernetes before passing it to Reconcile. An ObjectReconciler can be used in
// Builder.Complete by calling AsReconciler. See Reconciler for more details.
type ObjectReconciler[object client.Object] interface {
	Reconcile(context.Context, object) (Result, error)
}

// AsReconciler creates a Reconciler based on the given ObjectReconciler.
// 【中文研读】方法职责：把面向对象实例的控制器适配成面向 Request 的控制器；保存客户端与领域处理器，实际读取在后续 Reconcile 中发生。
func AsReconciler[object client.Object](client client.Client, rec ObjectReconciler[object]) Reconciler {
	// 【中文研读】处理流程：把面向对象实例的控制器适配成面向 Request 的控制器；保存客户端与领域处理器，实际读取在后续 Reconcile 中发生。
	return &objectReconcilerAdapter[object]{
		objReconciler: rec,
		client:        client,
	}
}

type objectReconcilerAdapter[object client.Object] struct {
	objReconciler ObjectReconciler[object]
	client        client.Client
}

// Reconcile implements Reconciler.
// 【中文研读】方法职责：TypedFunc 包装时直接调用函数；objectReconcilerAdapter 包装时先读取当前对象，忽略对象已不存在的错误，再交给领域 Reconciler。具体分支见各实现内部。
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

// TerminalError is an error that will not be retried but still be logged
// and recorded in metrics.
// 【中文研读】方法职责：包装明确不应自动重试的错误；控制器仍会记录错误，但不会按普通错误自动重新入队。
func TerminalError(wrapped error) error {
	// 【中文研读】处理流程：包装明确不应自动重试的错误；控制器仍会记录错误，但不会按普通错误自动重新入队。
	// 【中文研读】只增加“不自动重试”的错误分类，不执行补偿，也不把失败变成功。
	return &terminalError{err: wrapped}
}

type terminalError struct {
	err error
}

// Unwrap returns nil if te.err is nil.
// 【中文研读】方法职责：返回内部原始错误，使 Go errors 链式检查可以继续解包；内部为空则返回 nil。
func (te *terminalError) Unwrap() error {
	// 【中文研读】处理流程：返回内部原始错误，使 Go errors 链式检查可以继续解包；内部为空则返回 nil。
	return te.err
}

// 【中文研读】方法职责：构造可读错误文本；先处理内部错误为 nil 的特殊情况，防止直接调用空错误的方法。
func (te *terminalError) Error() string {
	// 【中文研读】处理流程：构造可读错误文本；先处理内部错误为 nil 的特殊情况，防止直接调用空错误的方法。
	if te.err == nil {
		return "nil terminal error"
	}
	return "terminal error: " + te.err.Error()
}

// 【中文研读】方法职责：支持 errors.Is 判断目标是否具有 terminalError 类型；比较的是错误类别，不是错误字符串。
func (te *terminalError) Is(target error) bool {
	// 【中文研读】处理流程：支持 errors.Is 判断目标是否具有 terminalError 类型；比较的是错误类别，不是错误字符串。
	tp := &terminalError{}
	return errors.As(target, &tp)
}
