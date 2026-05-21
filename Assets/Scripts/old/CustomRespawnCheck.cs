using UnityEngine;
using UnityEngine.Events;

namespace Oculus.Interaction.Samples
{
    public class CustomRespawnCheck : MonoBehaviour
    {
        /// <summary>
        /// 객체가 이 범위를 벗어나면 리스폰합니다.
        /// </summary>
        [SerializeField]
        [Tooltip("객체가 이 범위를 벗어나면 리스폰합니다.")]
        private Collider _respawnBoundaryCollider;

        /// <summary>
        /// 리스폰할 위치. 설정하지 않으면 초기 위치로 리스폰합니다.
        /// </summary>
        [SerializeField]
        [Tooltip("리스폰할 위치. 비워두면 초기 위치로 리스폰합니다.")]
        private Transform _respawnPositionOverride;

        /// <summary>
        /// 리스폰 시 발생하는 UnityEvent
        /// </summary>
        [SerializeField]
        [Tooltip("리스폰 시 발생하는 UnityEvent")]
        private UnityEvent _whenRespawned = new UnityEvent();

        /// <summary>
        /// 리스폰 후 이 프레임 수 동안 리지드바디를 키네마틱으로 설정하여 
        /// 고스트 충돌을 방지합니다.
        /// </summary>
        [SerializeField]
        [Tooltip("리스폰 후 이 프레임 수 동안 리지드바디를 키네마틱으로 설정합니다.")]
        private int _sleepFrames = 0;

        public UnityEvent WhenRespawned => _whenRespawned;

        private Vector3 _initialPosition;
        private Quaternion _initialRotation;
        private Vector3 _initialScale;

#pragma warning disable CS0618
        private TwoGrabFreeTransformer[] _freeTransformers;
#pragma warning restore CS0618
        private Rigidbody _rigidBody;
        private int _sleepCountDown;

        protected virtual void OnEnable()
        {
            _initialPosition = transform.position;
            _initialRotation = transform.rotation;
            _initialScale = transform.localScale;

#pragma warning disable CS0618
            _freeTransformers = GetComponents<TwoGrabFreeTransformer>();
#pragma warning restore CS0618
            _rigidBody = GetComponent<Rigidbody>();

            if (_respawnBoundaryCollider == null)
            {
                Debug.LogWarning("CustomRespawnCheck: Respawn Boundary Collider가 설정되지 않았습니다!", this);
            }
        }

        protected virtual void Update()
        {
            if (_respawnBoundaryCollider != null)
            {
                Collider thisCollider = GetComponent<Collider>();
                if (thisCollider != null && !_respawnBoundaryCollider.bounds.Intersects(thisCollider.bounds))
                {
                    Respawn();
                }
            }
        }

        protected virtual void FixedUpdate()
        {
            if (_sleepCountDown > 0)
            {
                if (--_sleepCountDown == 0)
                {
                    _rigidBody.isKinematic = false;
                }
            }
        }

        public void Respawn()
        {
            if (_respawnPositionOverride != null)
            {
                transform.position = _respawnPositionOverride.position;
                transform.rotation = _respawnPositionOverride.rotation;
            }
            else
            {
                transform.position = _initialPosition;
                transform.rotation = _initialRotation;
            }

            transform.localScale = _initialScale;

            if (_rigidBody)
            {
#pragma warning disable CS0618
                _rigidBody.velocity = Vector3.zero;
#pragma warning restore CS0618
                _rigidBody.angularVelocity = Vector3.zero;

                if (!_rigidBody.isKinematic && _sleepFrames > 0)
                {
                    _sleepCountDown = _sleepFrames;
                    _rigidBody.isKinematic = true;
                }
            }

            foreach (var freeTransformer in _freeTransformers)
            {
                freeTransformer.MarkAsBaseScale();
            }

            _whenRespawned.Invoke();
        }
    }
}
