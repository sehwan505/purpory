# Workagent 연동에서 확인한 Purpory 개선 TODO

> 이 문서는 Workagent 전용 기능을 Purpory에 추가하기 위한 계획이 아니다.
> 실제 연동에서 드러난 현상을 현재 Purpory 구현과 대조해, 범용 제품 차원의
> gap만 추적한다.

## 결론

이 작업은 진행할 가치가 있다. 다만 전체 요구를 한 번에 구현하지 않고,
재현된 P0 gap부터 작은 변경으로 나눈다. 이미 지원되는 기능과 Workagent 또는
연동 어댑터의 책임은 Purpory에서 다시 구현하지 않는다.

## 조사 결과

| 항목 | 현재 동작 | 기존 지원 기능 | 실제 gap | 제안 변경 | 호환성 위험 |
|---|---|---|---|---|---|
| 저장/embedding 분리 | DB 커밋 후 embedding을 실행한다. embedding 실패 시 서비스는 저장 결과와 오류를 함께 반환하지만 CLI는 결과를 출력하지 않는다. | 동일 hash 재저장은 `unchanged`이며 version 중복이 발생하지 않는다. | **Purpory gap 확인**: CLI에서 저장 성공 사실, key/hash/version을 알 수 없다. | `remember` 결과에 `persisted`, `key`, `hash`, `versionId`, embedding 상태와 오류를 포함한다. embedding 실패 시 exit 1은 유지한다. | JSON 필드 추가 위험은 낮다. 부분 성공 시 stdout이 생기는 계약은 문서화해야 한다. |
| no-embedding 검색 | 기본 모델 설정에서는 embedding을 호출하지 않고 substring seed와 graph ranking으로 검색한다. | 모델 없는 로컬 검색 자체는 이미 지원한다. | **Purpory gap 확인**: 명시적으로 선택할 방법과 실제 사용/실패 상태가 없다. provider 실패도 조용히 fallback한다. | `query --no-embed`와 같은 명시 모드 및 검색 상태 metadata를 추가한다. | 기존 JSON 결과에 필드를 추가하는 방식이면 위험이 낮다. |
| project root 충돌 | `SaveProject`가 ID 충돌 시 root를 무조건 UPSERT한다. | root는 `Abs`/`EvalSymlinks`로 정규화하며 Git worktree는 별도 View로 모델링한다. | **Purpory gap 확인**: 동일 ID와 다른 root가 조용히 덮어써진다. | 기본 `project add`는 create-or-idempotent로 제한하고, root 변경은 expected root를 사용하는 CAS로 명시한다. | 기존에 `project add`로 root를 바꾸던 호출자는 변경이 필요하다. |
| discovery ignore | `.git`, `build`, `dist`, `node_modules`, `vendor` 등을 모든 depth에서 제외하고 symlink도 제외한다. | generated/dependency 디렉터리 일부와 repository boundary는 이미 지원한다. | **부분 gap**: `.venv`가 포함된다. `.gitignore`와 사용자 exclude는 지원하지 않는다. | 우선 `.venv`/`venv` 제외와 기존 계약의 테스트·문서화를 추가한다. | 새 제외 디렉터리 안의 기존 Material이 사라질 수 있다. |
| 구조화 오류 | 성공 결과는 JSON을 지원하지만 오류는 stderr 일반 문자열이다. | 결과 0개는 exit 0, 검색 실패는 exit 1로 이미 구분한다. | **Purpory gap 확인**: project/record/storage 오류를 안정적인 code로 분기할 수 없다. | JSON 요청 시 `{error:{code,message,retryable}}` 형태를 제공하고 신뢰성 있게 감지 가능한 상태만 코드화한다. | stderr 문자열에 의존하던 호출자에게 영향이 있을 수 있다. |

## 재현 결과

- 저장 성공 + embedding 실패
  - exit `1`
  - stdout `0 bytes`
  - stderr에 embedding provider 연결 오류 출력
  - 다른 프로세스에서 같은 key를 조회하면 한국어 값, hash, version ID가 존재
- 같은 저장 요청 재시도
  - 다시 exit `1`
  - version은 여전히 1개여서 의도하지 않은 중복은 없음
- 동일 project ID를 다른 root로 다시 추가
  - exit `0`
  - 기존 root가 새 root로 조용히 변경됨
- discovery
  - `node_modules`는 제외됨
  - `.venv`와 `.gitignore`가 제외하도록 지정한 파일은 포함됨
- 빈 검색
  - exit `0`
  - `{"matches":[],"nodes":[],"edges":null}` 반환
- project/record not found 및 corrupt DB
  - 모두 exit `1`
  - machine-readable error code 없이 일반 문자열만 반환

## 구현 TODO

### P0 — `remember` 부분 성공 계약

- [ ] 저장 성공 + embedding 성공 결과를 구조화한다.
- [ ] 저장 성공 + embedding 실패 시 저장 결과와 embedding 오류를 동시에 반환한다.
- [ ] 저장 자체 실패와 부분 성공을 구분한다.
- [ ] stable memory key, content hash, version ID를 반환한다.
- [ ] 부분 성공 후 같은 내용을 재시도해도 version 중복이 생기지 않는 테스트를 추가한다.
- [ ] embedding 실패는 성공으로 숨기지 않고 exit 1을 유지한다.

새 opaque memory ID나 indexing 상태 테이블은 추가하지 않는다. 기존 key, hash,
version 및 계산 가능한 embedding status를 재사용한다.

### P0 — 명시적 no-embedding 검색

- [ ] embedding을 시도하지 않는 명시적 CLI/API 계약을 추가한다.
- [ ] lexical/exact 검색 사용 여부를 결과에 표시한다.
- [ ] vector 검색 요청 및 실제 사용 여부를 표시한다.
- [ ] 의도적인 no-embedding과 embedding 장애 fallback을 구분한다.
- [ ] fallback 시 degraded 상태와 embedding 오류를 숨기지 않는다.
- [ ] offline/local-only 회귀 테스트를 추가한다.

기본 설정에서 이미 동작하는 substring seed + Typed PPR 경로를 재사용한다.

### P0 — project root CAS

- [ ] 신규 project 등록을 유지한다.
- [ ] 동일 ID + 정규화된 동일 root는 idempotent success로 처리한다.
- [ ] 동일 ID + 다른 root는 기본적으로 conflict를 반환한다.
- [ ] expected current root가 일치할 때만 명시적인 root 변경을 허용한다.
- [ ] stale expected root의 CAS conflict를 테스트한다.
- [ ] symlink/path normalization 및 Git worktree View 동작을 유지한다.

Workagent 전용 단일 cwd 정책은 추가하지 않는다.

### P1 — 구조화 CLI 오류

- [ ] JSON 출력 요청 시 machine-readable error envelope를 제공한다.
- [ ] `PROJECT_NOT_FOUND`와 storage failure를 구분한다.
- [ ] `RECORD_NOT_FOUND`와 storage failure를 구분한다.
- [ ] 감지 가능한 경우 DB busy/open/corrupt/permission 오류를 구분한다.
- [ ] 정상 검색 결과 0개는 성공과 빈 배열을 유지한다.
- [ ] 기존 exit code `0` 성공, `1` 실행 실패, `2` 사용법 오류를 가능한 한 유지한다.

모든 SQLite 오류를 추측으로 세분화하지 않는다. 드라이버 또는 `errors.Is`/
`errors.As`로 신뢰성 있게 식별 가능한 상태만 symbolic code로 노출한다.

### P1 — discovery 제외 계약

- [ ] `.venv`와 `venv` 디렉터리 제외 여부를 제품 계약으로 확정한다.
- [ ] nested built-in ignore rule 테스트를 추가한다.
- [ ] symlink 제외 테스트를 추가한다.
- [ ] repository boundary 테스트를 추가한다.
- [ ] Unicode 경로 테스트를 추가한다.
- [ ] 현재 built-in 제외 목록을 문서화한다.

완전한 `.gitignore` parser와 임의 사용자 pattern은 실제 제품 요구가 확인된 뒤
별도 범위로 검토한다. Workagent 전용 디렉터리는 하드코딩하지 않는다.

## 유지해야 할 기존 계약

- [ ] stable key로 저장한 내용을 다시 조회할 수 있다.
- [ ] correction/revision은 기존 version을 파괴적으로 덮어쓰지 않는다.
- [ ] 프로세스 재시작 후에도 데이터가 유지된다.
- [ ] 명시적인 project ID는 cwd와 독립적으로 동작한다.
- [ ] 프로젝트별 데이터 격리를 유지한다.
- [ ] source/revision metadata를 유지한다.
- [ ] 한국어와 Unicode 문자열이 정확히 왕복한다.
- [ ] 긴 CLI 입력의 지원 범위와 제한을 characterization test로 남긴다.
- [ ] JSON 성공 결과의 기존 필드와 결정성을 가능한 한 유지한다.

## 구현하지 않을 항목

- Workagent 작업 상태 또는 상태 머신
- 사용자 권한과 실행 권한
- 작업 예산과 완료 조건
- Slack/회의 의미 해석
- 자동 작업 재개 또는 agent retry 정책
- correction이 어떤 사용자 작업에 적용되는지 판단하는 로직
- 저장된 기억을 근거로 agent 권한을 확대하는 기능
- Workagent 전용 cwd 정책

검색된 기억은 데이터이며 실행 권한이 아니다.

## 조사 시 실행한 검증

다음 테스트는 조사 시 실제로 실행했고 통과했다.

```sh
go test ./internal/material ./internal/project ./internal/store ./internal/app ./internal/cli
go test -run 'TestQueryFallsBackWhenSemanticSearchTimesOut|TestMemoryRoundTrip|TestProjectRoundTrip|TestDiscoverAndDiff|TestObserveGitIncludesEveryWorktree' -v ./internal/app ./internal/store ./internal/material ./internal/project
```

## 관련 구현

- `internal/app/service.go`: project 등록, remember, query, update orchestration
- `internal/app/embeddings.go`: embedding sync와 semantic fallback
- `internal/app/context_graph.go`: local substring seed와 graph retrieval
- `internal/store/memory.go`: transactional memory 저장과 hash idempotency
- `internal/store/project.go`: project UPSERT와 cwd resolution
- `internal/material/material.go`: discovery와 built-in ignore
- `internal/cli/command.go`: command별 JSON 출력
- `internal/cli/run.go`: process exit code와 stderr 오류 출력

## 권장 작업 순서

1. `remember` 부분 성공 계약
2. 명시적 no-embedding 검색과 검색 상태
3. project root CAS
4. 핵심 구조화 오류 코드
5. `.venv` 제외와 discovery characterization tests

각 단계는 계약을 증명하는 테스트를 먼저 추가하고 최소 구현 후 focused test,
관련 전체 test suite, backward compatibility 순으로 확인한다.
