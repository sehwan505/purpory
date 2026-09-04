# 그래프 수명주기 계획

## 목표

Purpory는 빈 데이터베이스에서 유용한 프로젝트 그래프 하나를 만들기
시작한다. 관찰한 프로젝트 상태를 최신으로 유지하고, 사용자 근거가 있는
지속 Intent만 추가하며, 에이전트가 이미 탐색한 경로를 이용해 다음에 볼
작은 문맥을 선택한다.

목표는 가장 조밀한 그래프가 아니다. 검색 품질을 높일 만큼 관계를 신뢰할
수 있는 가장 작은 그래프다.

## 현재 기준선

이미 구현된 항목:

- `setup`이 Project를 명시적으로 등록하고 Resource를 배정한 뒤 `update`를
  실행하고 에이전트 연동 하나를 설치한다.
- `update`가 Material을 발견하고, 바뀌지 않은 추출 결과를 점진적으로
  재사용하며, 구조적 claim을 해석하고, 관찰 snapshot을 원자적으로 교체한다.
- 지속 memory와 관찰 knowledge가 물리적인 `nodes`, `edges` 테이블을
  공유하면서도 서로 다른 owner를 유지한다.
- 지속 link는 관찰 갱신 후에도 남는다. 사라진 관찰 대상은 `missing`이
  되며 나중에 다시 연결될 수 있다.
- exact match와 embedding match가 Typed PPR 검색의 seed가 된다.
- `prepare`가 최근에 연 node를 감쇠 seed로 사용하고, 현재 Session에 이미
  전달한 node는 다시 추천하지 않는다.
- transcript가 text, choice, tool call/result, attachment part를 순서 있는 event로
  정규화하고, 구조화된 사용자 선택을 assistant reply로 복원한다.
- reconciliation이 USER `evidenceRefs`와 사용자가 채택한 assistant excerpt의
  `contextRefs`를 분리해 exact quote와 byte interval을 검증하고 audit에 보존한다.
- reconciliation이 semantic/lexical match, 1-hop 이웃, 같은 batch Intent를
  제한된 후보로 구성하고 최대 20개씩 한 번의 relation pass로 연결한다.
- 저장 경계가 Intent→Material과 Intent→Intent link를 같은 transaction과
  audit에서 처리하고 대칭 관계를 결정적으로 정규화한다.

현재 남은 항목:

- 고정 corpus를 이용한 품질·비용 회귀 측정은 아직 없다. 제품 사용 데이터가
  생기기 전에는 임의 가중치 조정이나 관계 enum 확장을 하지 않는다.

## 경계

서로 다른 세 종류의 상태를 분리한다.

```text
canonical graph       현재 프로젝트의 의미와 근거
audit history         지속 node나 edge가 변경된 이유
navigation trail      한 Session이 검색하고 이동한 순서
```

canonical graph만 지속 프로젝트 지식으로 취급한다. audit과 navigation 기록은
검증이나 순위에 영향을 줄 수 있지만 graph node나 canonical edge가 되지는
않는다.

### 소유권

| Owner | 생성 주체 | 교체할 수 있는 주체 | 예시 |
|---|---|---|---|
| observed | `update` adapter | 다음 명시적 `update` | Material, section, function, `contains`, `calls` |
| durable | 명시적 memory 또는 reconciliation | 사용자 근거가 있는 명시적 reconciliation 또는 삭제 | Intent, reference, semantic edge |
| operational | prepare/query/explain/path | Session 보존 정책 | navigation event, decision, delivery |

`update`는 지속 Intent를 추론하거나 retire해서는 안 된다. reconciliation은
갱신된 관찰 snapshot을 후보 문맥으로 사용할 수 있지만, 모든 지속 변경의
권위는 여전히 사용자 근거에 있다.

## 범용성 계약

범용성은 모든 provider의 형식을 core가 직접 이해하는 방식으로 만들지 않는다.
각 provider가 입력을 작은 domain-neutral 계약으로 정규화하고, 그 이후의
graph lifecycle은 같은 코드를 사용하는 방식으로 만든다.

| 경계 | 정규화 계약 | 현재 지원 | 확장 방법 |
|---|---|---|---|
| model 생성 | strict structured output | Ollama, OpenAI-compatible | provider adapter가 schema 출력과 오류를 정규화 |
| embedding | text batch → vector batch | Ollama, OpenAI-compatible | model identity와 dimension을 분리해 adapter 추가 |
| transcript | 순서가 있는 `id`, `role`, `text`, 선택적 `replyToId` | Codex/Claude JSONL의 text 중심 해석 | agent별 transcript adapter가 tool/UI 선택을 동일 message로 변환 |
| Material 발견 | 안정적 URI, media type, revision/hash, size | 로컬 folder와 Git View의 file | 외부 Resource adapter가 같은 Material manifest 생성 |
| 내용 읽기·추출 | media type별 content reader와 processor | text/Markdown/source adapter | 문서, 대화, media, 외부 reference processor 추가 |
| graph·검색 | domain-neutral node, edge, vector | 모든 정규화된 입력 | provider 분기 없이 동일 lifecycle 사용 |

core는 Ollama, OpenAI, Codex, Claude, Git, 특정 파일 형식을 조건문으로
판단해서는 안 된다. provider별 인증, pagination, retry, transcript 모양,
content 다운로드는 adapter 경계에 남긴다.

사용자가 assistant 선택지에 답하는 경우에는 `replyToId`가 있으면 우선
사용하고, 없으면 같은 transcript의 바로 앞 assistant message만 제한적으로
참조한다. 구조화된 버튼 선택이나 tool UI 응답도 adapter가 USER message와
참조 대상 ID로 변환한다. 따라서 provider가 달라도 USER는 권위, ASSISTANT는
선택된 참조문이라는 규칙은 변하지 않는다.

외부 정보 provider는 안정적인 URI와 변경 식별자를 제공해야 한다. 내용을
읽을 수 없는 binary도 Material로 catalog할 수 있지만, processor가 없으면
내용이나 추론 관계를 만들지 않는다. embedding provider를 바꾸면 vector만
다시 만들며 canonical graph를 다시 생성하지 않는다. model provider 장애도
zero-base `update`와 exact/structural 검색을 막지 않는다.

현재 `material.Discover`와 추출 경로는 로컬 filesystem에 직접 연결되어
있으므로 정보 provider 범용성은 아직 완성되지 않았다. 실제 두 번째 Resource
provider를 구현할 때 consuming package에 작은 observer/reader 경계를 만들고,
그 전에는 사용되지 않는 plugin registry나 범용 interface를 미리 만들지 않는다.

### Provider를 하나의 개념으로 합치지 않는다

`provider`라는 이름 아래 서로 다른 책임을 넣으면 인증, 파일 읽기, transcript
신뢰도, model 비용 정책이 한 추상화에 섞인다. Purpory는 다음 네 경계를 서로
독립적으로 취급한다.

| 종류 | 입력 | 정규화된 출력 | 만들 수 있는 상태 | 만들 수 없는 상태 |
|---|---|---|---|---|
| workspace observer | 로컬 경로 또는 provider 관찰 지점 | `Resource`, `View` | operational topology | Project, canonical edge |
| Material source | 배정된 `Resource` | Material manifest와 content stream | observed Material | durable Intent, durable edge |
| transcript decoder | agent transcript | 순서가 있는 `Message` | reconciliation 입력 | memory, canonical edge |
| model adapter | prompt 또는 text batch | schema 결과, vector, usage | 후보와 검색 신호 | 권위 있는 변경 |

같은 vendor가 여러 경계를 구현할 수는 있지만 계약을 공유하지 않는다. 예를
들어 OpenAI model adapter를 추가해도 OpenAI가 Material source가 되는 것은
아니다. Slack 대화를 Material로 읽는 adapter와 Slack 기반 agent transcript를
해석하는 decoder도 서로 다른 입력 계약이다.

### 전체 데이터 흐름

```text
provider 원본
  │
  ├─ workspace adapter ───────────▶ Resource / View
  │                                  operational only
  │
  ├─ Material adapter
  │    인증 · pagination · retry
  │        │
  │        ├─ catalog ─────────────▶ Material manifest
  │        └─ open ────────────────▶ bounded content stream
  │                                      │
  │                               media-type extractor
  │                                      │
  │                                      ▼
  │                              observed Node / Claim
  │
  ├─ transcript adapter ──────────▶ ordered Message
  │                                      │ untrusted
  │                               reconciliation
  │                                      │ validated USER evidence
  │                                      ▼
  │                              durable Memory / Link
  │
  └─ model adapter ───────────────▶ structured result / vector / usage

                         atomic validation and publish
                                      │
                                      ▼
                     SQLite canonical + audit + operational
```

adapter가 반환한 값은 곧바로 graph 변경 권한을 얻지 않는다. Material 경로는
항상 `observed` 결과만 만들고, transcript와 model 경로는 USER evidence 검증을
통과한 뒤에만 `durable` 결과를 만든다. 이 규칙이 provider 종류보다 먼저
적용된다.

### 정규화 데이터 계약

#### Resource와 View

- `Resource.Provider`는 adapter를 선택하는 안정적인 소문자 ID다.
- `Resource.Identity`는 provider 계정 안에서 대상 공간을 다시 찾을 수 있는
  안정적인 식별자다. 표시 이름, 로컬 경로, access token을 넣지 않는다.
- `View`는 관찰 가능한 snapshot을 뜻한다. 로컬 provider는 root와 Git revision을,
  외부 provider는 snapshot revision과 availability를 사용할 수 있다.
- `View.Root`는 로컬 adapter의 선택 필드다. 외부 provider가 URL이나 object ID를
  억지로 Root에 넣지 않고 Resource identity와 provider 전용 adapter로 접근한다.
- 인증 정보와 pagination cursor는 Resource에 넣지 않는다. 인증은 전역 provider
  설정, cursor는 Resource별 operational checkpoint로 분리한다.
- observation은 기존 Project에 Resource/View를 붙일 수 있지만 Project를 만들 수
  없다.

#### Material manifest

provider가 content를 읽기 전에 다음 값만 catalog한다.

| 필드 | 규칙 |
|---|---|
| resource ID | 어느 배정 Resource에서 왔는지 식별한다 |
| stable URI | rename 가능한 label이 아니라 provider object identity로 만든다 |
| media type | extractor 선택에 쓰는 표준 MIME type이다 |
| revision | content가 바뀌었는지 판단하는 provider change token이다 |
| size/time | 알 수 있을 때만 채우는 metadata다 |

Material ID는 `resource ID + stable URI`에서 결정하고 content revision으로 만들지
않는다. 내용이 바뀌어도 같은 Material이어야 기존 durable link가 유지된다.
provider가 신뢰할 revision을 주지 않으면 adapter가 bounded content hash를
계산한다. 현재 `Hash`는 이 change identity 역할까지 겸하고 있으므로 첫 외부
Material source를 추가할 때 `ResourceID`와 `Revision`의 필요 여부를 migration과
함께 확정한다.

URI 예시는 다음 원칙만 지키면 된다.

```text
file:docs/ARCHITECTURE.md
notion:page/01234567
slack:channel/C123/message/1720000000.000100
```

URI 구조를 core가 파싱하지 않는다. 해당 Material을 만든 adapter만 URI를 content
locator로 해석한다. label이나 URL이 바뀌어도 provider object ID가 같으면 stable
URI를 유지한다.

#### Content stream과 extractor

Material source의 읽기 경계는 경로가 아니라 `context.Context`를 받는 bounded
`io.Reader`/`io.ReadCloser`다. 다운로드, 인증, retry, 최대 응답 크기는 source
adapter가 책임진다. extractor는 Material metadata와 stream만 받아 다음 값을
반환한다.

```text
Facts { observed Nodes, unresolved Claims }
```

source adapter는 가능한 한 원본을 표준 MIME content로 정규화한다. 예를 들어
Notion block은 먼저 `text/markdown` 또는 `text/plain` stream으로 변환해 기존
extractor를 재사용한다. provider 전용 의미가 실제로 필요한 경우에만 별도
processor를 추가한다. 지원하지 않는 binary는 Material node만 catalog한다.

#### Transcript message

모든 agent transcript는 다음 최소 의미로 정규화한다.

```text
Event { id, sequence, role, kind, text, optional replyToId, optional callId, parts[] }
Part  { id, kind, text, optional name, optional ref }
```

- `sequence`는 파일 행 번호가 아니라 사용자에게 보인 대화 순서다.
- role은 `user`, `assistant`, `system`, `tool`을 보존한다. system/tool 출력은
  USER evidence가 될 수 없다.
- 구조화된 선택 버튼은 adapter가 선택 label을 USER text로 만들고 원래 assistant
  message를 `replyToId`로 연결한다.
- 원본이 reply 정보를 주지 않을 때만 직전 assistant message를 fallback으로
  사용할 수 있다. 여러 제안을 구분할 수 없으면 후보를 버린다.
- provider 원본 ID가 안정적이면 보존하고, 없으면 transcript 내부 순서에서
  결정적으로 만든다.

decoder는 신뢰도를 높이는 정규화만 할 뿐 memory 후보를 만들지 않는다.
USER 권위, assistant context, evidence/context ID 순서 검증은 provider와 무관한
reconciliation 코드가 한 번만 수행한다.

#### Model 결과

model 역할은 계속 별도 binding으로 둔다.

- gate와 reconciliation은 strict schema 결과만 요청한다.
- embedding은 text batch와 기대 dimension을 받아 같은 순서의 vector batch를
  반환한다.
- adapter는 provider별 schema 문법, timeout, retry 가능한 오류, 응답 크기,
  token usage를 정규화한다.
- 호출자는 provider 이름이 아니라 필요한 작은 역할 계약만 사용한다.
- provider가 사용량을 제공하면 input/output/cached token을 audit에 기록하고,
  제공하지 않으면 `unknown`으로 남긴다. 추정치를 과금 사실처럼 저장하지 않는다.

model 결과는 제안 또는 검색 신호다. store validation을 우회하거나 transaction을
직접 열 수 없다. model이 전부 unavailable이어도 update, exact search, structural
walk, 수동 memory는 동작한다.

### 책임 배치

새로운 공용 `provider`, `adapter`, `interfaces` package는 만들지 않는다. 현재
package 책임을 유지한다.

| 위치 | 책임 |
|---|---|
| `internal/project` | domain-neutral Resource/View와 현재 local observation |
| `internal/material` | manifest, identity, diff와 현재 local catalog |
| `internal/extract` | MIME content를 observed Facts로 변환 |
| `internal/reconcile` | 정규 Message와 USER evidence 검증 |
| `internal/app` | 작은 consuming interface, adapter 선택, lifecycle 조정 |
| `internal/<provider>` | 인증, HTTP/API 모양, pagination, provider 오류 정규화 |
| `internal/store` | 원자적 publish와 canonical 불변 조건 |

두 번째 구현이 생기면 `main`이 `provider ID → adapter`의 명시적인 작은 map을
구성해 `app`에 넘긴다. 구현이 스스로 전역 registry에 등록하거나 runtime plugin을
검색하지 않는다. CLI와 Wails는 같은 `app` 구성을 호출하며 provider별 lifecycle을
복제하지 않는다.

첫 외부 source가 생길 때 예상하는 가장 큰 seam도 다음 두 동작뿐이다. 정확한
이름은 구현 위치에서 정하되 더 넓은 범용 인터페이스로 키우지 않는다.

```go
type materialSource interface {
	Snapshot(context.Context, project.Resource) (project.Resource, []material.Material, error)
	Open(context.Context, material.Material) (io.ReadCloser, error)
}
```

`Snapshot`은 갱신된 Resource 관찰과 완전한 Material manifest를 함께 반환한다.
`Open`은 그 adapter가 만든 Material만 읽는다. transcript 쪽은 실제 두 번째
decoder가 서로 다른 parsing을 요구할 때 `Decode(io.Reader) ([]Message, error)`
한 동작만 추출한다. model 쪽은 이미 `app`이 소비하는 generation/embedding
인터페이스를 유지한다.

### 실패와 snapshot 계약

- Resource catalog는 완전한 snapshot 단위다. pagination 중간에 실패하면 그
  Resource의 일부 결과를 게시하지 않는다.
- 배정된 Resource 하나라도 필수 관찰에 실패하면 기존 project snapshot을
  유지한다. 빈 결과로 교체해 삭제로 오해하지 않는다.
- revision이 바뀐 Material의 extraction이 실패하면 stale facts를 유지하지 않고
  Material node만 게시하며 warning을 남긴다. revision이 같으면 기존 facts를
  재사용하고 content를 다시 읽지 않는다.
- 인증 실패, rate limit, timeout, unsupported media를 구분해 반환하되 provider
  원본 응답이나 secret을 graph와 기본 log에 넣지 않는다.
- delta cursor는 snapshot commit 뒤에만 전진시킨다. 처음에는 full catalog를
  사용하고 실제 비용이나 제한이 확인될 때만 delta 관찰을 추가한다.
- reconcile/model 실패는 durable graph를 부분 변경하지 않는다. retry job과
  audit만 operational 상태로 남긴다.

### 새 provider를 추가하는 절차

#### 새로운 정보 source

1. provider의 안정적인 Resource ID, Material URI, revision 규칙을 먼저 문서화한다.
2. 기존 local 동작과 새 source가 함께 통과할 작은 contract test를 만든다.
3. 그때 `app`의 consuming 위치에 catalog/open interface를 추출한다.
4. local `Discover`와 path read를 첫 adapter로 옮기되 동작은 바꾸지 않는다.
5. 새 adapter가 manifest와 bounded stream을 반환하게 한다.
6. 기존 extractor로 표현할 수 없는 MIME만 최소 processor로 추가한다.
7. 두 Resource를 함께 update해 stable ID, diff, atomic publish, durable link 보존을
   E2E로 검증한다.

contract test는 최소 다음을 증명한다.

- 같은 원격 object의 content 변경 전후 Material ID는 같다.
- revision이 같으면 content를 다시 읽지 않는다.
- partial pagination 실패가 기존 snapshot을 지우지 않는다.
- 지원하지 않는 binary도 catalog되며 내용은 저장되지 않는다.
- provider secret과 개인 경로가 graph, audit, 오류 문자열에 들어가지 않는다.

#### 새로운 transcript 형식

1. 실제 fixture에서 USER, ASSISTANT, reply/choice 표현만 식별한다.
2. 두 번째 형식이 생길 때 `reconcile`의 consuming 위치에 decoder seam을 만든다.
3. 정규 Message fixture가 provider와 무관한 동일 reconciliation test를 통과하게
   한다.
4. tool output 단독, 응답 없는 제안, 모호한 긍정이 memory를 만들지 않는지
   검증한다.

#### 새로운 model backend

1. 필요한 역할이 generation인지 embedding인지 하나만 구현한다.
2. provider package 안에서 wire format을 domain 결과로 바꾼다.
3. timeout, cancellation, size limit, strict schema, dimension을 contract test로
   확인한다.
4. `main`의 명시적 구성과 설정 validation에 provider ID를 추가한다.
5. 기존 role binding, graph schema, reconciliation 정책은 변경하지 않는다.

### 구현 시점

transcript의 `replyToId/contextRefs`는 구현되었다. 다음으로 필요한 코드는 Intent
link와 navigation trail처럼 이미 실패 사례가 확인된 graph lifecycle이다. Material
source interface, generic adapter map, cursor 저장소는 실제 첫 외부 source를
선택할 때 추가한다. 이 순서라면 현재 로컬 흐름을 불필요하게 흔들지 않으면서도
어디를 잘라 확장할지는 이미 결정되어 있다.

## Canonical 관계 계약

### Intent→Intent

| 관계 | 방향 | 의미 |
|---|---|---|
| `refines` | source → target | source가 target을 더 구체화한다 |
| `depends_on` | source → target | source가 성립하려면 target이 필요하다 |
| `conflicts_with` | 대칭 | 두 Intent가 동시에 성립할 수 없다 |

`conflicts_with`는 endpoint를 결정적인 순서로 정규화해 한 번만 저장한다.
나머지 관계는 방향을 보존한다. self-link는 허용하지 않는다.

초기에는 `related_to`를 저장하지 않는다. 단순 관련성은 이미 embedding 검색
신호로 얻을 수 있다. 모든 유사성을 edge로 만들면 조밀하고 설명하기 어려운
이웃이 생긴다. 실제 평가에서 semantic seed가 필요한 multi-hop bridge를
놓친다는 사실이 확인될 때만 `related_to`를 추가한다. 이때도 추론 기본값이
아니라 사용자 근거가 있고, 대칭이며, 가중치가 낮은 fallback으로 제한한다.

`refined_by`, `enables` 같은 역관계 이름은 추가하지 않는다. 탐색이 이미
edge를 양방향으로 읽기 때문이다. 서로 다른 key 사이의 교체 사례가 테스트
corpus에 나타나기 전에는 `supersedes`도 추가하지 않는다. 같은 key의 교체는
memory version 갱신으로 처리한다.

### Intent→근거

기존 관계를 유지한다.

- `applies_to`: Intent가 대상의 범위나 제약을 정한다.
- `realized_by`: 대상이 의도한 결과를 구현한다.
- `verified_by`: 대상이 Intent 충족을 확인한다.
- `contradicted_by`: 대상이 Intent와 충돌한다.

구조 adapter의 관계는 adapter가 소유하는 문자열로 유지한다. 이 관계 때문에
domain-neutral core의 관계 어휘를 확장해서는 안 된다.

## 수명주기

### 1. 빈 데이터베이스에서 유용한 관찰 그래프까지

1. `setup`이 Project와 Resource를 명시적으로 등록한다.
2. `update`가 배정된 각 Resource에서 사용 가능한 View 하나를 관찰한다.
3. discovery가 안정적인 Material ID와 URI를 만든다.
4. 형식 adapter가 모델 없이 node와 미해결 claim을 추출한다.
5. 프로젝트 전체 resolution이 안정적인 reference를 연결한다.
6. 하나의 transaction이 전체 관찰 snapshot을 게시한다.
7. 선택적인 embedding sync가 활성 Intent와 Knowledge node를 색인한다.

빈 프로젝트에 Intent가 없는 것은 정상이다. 문서와 코드는 관찰된 근거이며,
사용자의 권위 있는 결정으로 자동 승격하지 않는다. 첫 번째 명시적 `remember`
또는 근거가 있는 Session reconciliation이 첫 지속 Intent를 만든다.

모든 model provider를 꺼도 이 경로는 유용해야 한다. exact search, 구조적
graph traversal, 수동 memory는 계속 작동해야 한다.

### 2. Session reconciliation이 지속 Intent를 성장시킨다

전체 그래프를 한 번에 prompt에 넣지 않고, 범위가 제한된 두 번의 model
pass를 사용한다.

#### Pass A: 근거 있는 후보 추출

신뢰할 수 없는 transcript를 읽고 USER 발언을 유일한 권위로 삼아 memory
후보를 추출한다. 사용자가 결정을 직접 말했다면 그 USER message만 근거로
사용한다. 사용자가 assistant의 제안을 명시적으로 선택했다면 USER message는
권위 근거로, 선택된 assistant message는 내용을 해석하기 위한 참조문으로
함께 보존한다. 이 단계에서는 Intent→Intent link를 만들지 않는다.

후보는 권위 근거와 해석 문맥을 구분한다.

- `evidenceRefs`: 결정을 승인하거나 직접 말한 USER message/part ID, 정확한
  quote, 검증된 byte interval
- `evidenceIds`: UI 호환과 요약을 위해 `evidenceRefs`에서 결정적으로 파생한
  USER message ID
- `contextRefs`: 사용자가 선택한 내용을 담은 ASSISTANT message/part ID,
  정확한 quote, 검증된 byte interval

model은 ID와 quote만 제안한다. byte interval은 core가 원문에서 유일하게
일치하는 quote를 찾아 계산하며, 일치하지 않거나 같은 part에 여러 번 나타나
위치가 모호하면 후보를 버린다. 이 source-grounding 경계는 Google
LangExtract의 exact source alignment 아이디어를 provider-neutral하게 차용한다.

예를 들어 assistant가 `1. SQLite, 2. PostgreSQL`을 제시하고 사용자가
`2번으로 하자`고 답했다면, 최종 값은 PostgreSQL을 선택한 결정이며 USER
message가 권위, assistant message가 참조문이다. assistant 제안만 있거나,
사용자가 답하지 않았거나, `좋아`가 여러 제안 중 무엇을 가리키는지 모호하면
후보를 만들지 않는다.

assistant message는 단독 evidence가 될 수 없다. 사용자 선택으로 가리킨
부분만 내용을 보완할 수 있으며, 선택되지 않은 설명이나 제안은 지속 memory로
승격하지 않는다.

#### 후보 검색

각 decision 후보마다 다음 항목에서 기존 Intent 이웃을 제한적으로 구성한다.

1. 같은 key를 가진 현재 memory가 있으면 그 memory
2. `key + value`에 대한 semantic Intent match 최대 8개와 embedding이 없을 때의
   제한된 lexical fallback
3. 해당 match의 활성 1-hop Intent 이웃
4. 같은 Material/Knowledge 근거를 공유하는 활성 Intent
5. 현재 Session에서 최근 탐색한 Intent
6. 같은 reconciliation batch의 다른 decision 후보

model 호출 전에 중복을 제거하고 최종 후보 수를 제한한다. 기존 내용과 현재
물리 관계는 문맥일 뿐 evidence가 아니다.

#### Pass B: reconcile 및 연결

변경된 후보 묶음과 각 후보의 제한된 이웃을 하나의 structured-output 호출에
넣어 다음을 반환한다. 후보마다 model을 따로 호출하지 않는다.

- 현재 key, kind, value
- `refines`, `depends_on`, `conflicts_with`를 사용하는 0개 이상의 추가 관계
- 인용한 USER 수정 발언이 기존 관계가 더는 유효하지 않음을 입증할 때만
  명시적인 retirement

model 출력에서 빠졌다는 이유로 지속 edge를 retire하지 않는다. `none`은
유효하고 흔한 결과다. 처음에는 변경된 Intent 하나당 새 Intent link를 최대
4개까지만 허용한다. false-link와 orphan 측정 결과가 있을 때만 이 한도를
조정한다.

#### 비용 상한

- reconciliation은 매 message가 아니라 Session 종료 시에만 실행한다.
- 선택지 승인 해석은 Pass A에서 함께 처리하며 별도 model 호출을 만들지 않는다.
- 기존 Intent 후보 검색은 SQLite 검색과 batch embedding으로 처리한다.
- Pass B는 후보당 호출하지 않고 최대 20개 후보를 처리하되, encoded 요청이
  model context의 절반을 넘기 전에 batch를 나눈다. 단일 요청 자체가 넘으면
  관계를 일부 유실시키지 않고 명시적으로 실패한다.
- decision 후보가 없거나 연결할 기존 Intent 후보가 없으면 Pass B를 생략한다.
- 같은 key가 여러 transcript chunk에서 추출됐을 때만 consolidation을 실행한다.
- provider가 usage를 노출할 때 model 호출 수, input/output token, latency를
  reconciliation audit에 기록하는 것은 Phase 5 측정 단계에서 추가한다.

#### 결정적 검증

저장하기 전에 다음을 검증한다.

- 추가하거나 retire하는 모든 관계가 현재 Session의 USER evidence를 인용한다.
- `contextRefs`는 같은 transcript에서 해당 USER message보다 앞선 assistant
  part와 그 안에 실제로 존재하는 정확한 quote만 가리킨다.
- 간접 선택은 하나의 제안이나 번호를 명확하게 식별해야 하며, 단순 긍정이나
  침묵을 승인으로 해석하지 않는다.
- source는 해당 후보 Intent다.
- target은 허용된 기존 Intent 또는 검증된 동일 batch Intent다.
- source와 target이 다르다.
- node-kind 조합에 허용된 관계다.
- 대칭 관계의 endpoint가 정규화되어 있다.
- 중복 작업을 결정적으로 하나로 합친다.
- consolidation이 evidence, context, target, link를 새로 만들어내지 않는다.

memory version, 현재 node, edge 변경, reconciliation audit을 하나의
transaction으로 저장한다. Pass B가 본 edge 상태와 외부 Intent endpoint 상태도
transaction 안에서 다시 확인한다. 동시성 충돌이 나면 한 번만 재시도하되,
두 번째 판단이 최신 상태를 보도록 후보 검색부터 다시 실행한다.

### 3. 관찰 갱신이 근거를 유지한다

현재 snapshot 알고리즘을 유지한다.

1. 배정된 모든 Resource를 발견한다.
2. 안정적인 Material identity와 content hash로 diff한다.
3. 변경되지 않은 추출 node와 claim을 재사용한다.
4. 변경된 Material을 다시 추출한다.
5. 합쳐진 전체 snapshot을 resolve한다.
6. observed node와 edge만 원자적으로 교체한다.
7. 관찰 대상이 사라지면 지속 endpoint를 `missing`으로 보존한다.
8. 같은 안정적 reference가 돌아오면 `active`로 복구한다.
9. 변경된 활성 후보의 embedding만 갱신하고 오래된 vector는 제거한다.

Material이 바뀌었다는 이유만으로 Intent edge를 자동 retire하지 않는다.
review item이나 `contradicted_by` 제안은 사용자 근거가 있는 reconciliation을
통해서만 만들 수 있다.

### 4. Navigation trail이 점진 검색을 안내한다

탐색 순서의 근거를 마지막 delivery upsert에서 append-only operational
trail로 바꾼다.

```text
navigation_events(
  id, project_id, session_id, action,
  source_node_id, target_node_id, decision_id, created_at
)
```

`query`, `explain`, `path`를 정확한 삽입 순서로 기록한다. 기본적으로 원문
query는 보존하지 않는다. `context_decisions`가 이미 hash를 보존하며 필요할
때만 원문 보존을 선택할 수 있다.

Session을 인식하는 검색마다 다음 순서를 적용한다.

1. 현재 요청의 semantic match를 주 seed로 사용한다.
2. exact match를 주 seed로 사용한다.
3. 최근 navigation node 8개를 감쇠 continuity seed로 사용한다.
4. 반복 방문은 가중치를 더하므로 되돌아간 뒤의 초점을 복구한다.
5. 실제 `explain`으로 연 node를 suppression set으로 사용한다.
6. 합쳐진 분포에서 Typed PPR을 확장한다.
7. 아직 열지 않았고 활성 상태이며 내용이 있는 signpost를 최대 3개 반환한다.

trail은 순위만 바꾸며 프로젝트 관계를 만들지 않는다. Session ID가 없는
직접 `query`는 stateless로 유지한다. 에이전트 `prepare`와 Session을 인식하는
query는 같은 검색 구현을 사용한다.

### 5. 지속 edge 수명주기

`edges`는 현재 projection으로, `reconciliation_events`는 history로 취급한다.
관계 retirement를 구현할 때만 active/retired 상태와 timestamp를 추가한다.
검색은 활성 edge만 읽는다.

- 새로 근거가 확인된 관계는 현재 edge를 활성화하거나 재활성화한다.
- 명시적이고 근거가 있는 수정은 edge를 retire하고 event를 기록한다.
- `update`는 지속 edge를 retire할 수 없다.
- Intent 삭제는 기존 node 수명주기에 따라 현재 edge를 제거하지만, memory
  version과 reconciliation audit은 계속 보존한다.
- 일반 검색에서 과거 edge를 seed로 사용하지 않는다.

graph event node나 완전한 bi-temporal subsystem은 만들지 않는다. ingestion
time뿐 아니라 event time이 필요한 구체적인 temporal query가 생길 때만
validity interval을 추가한다.

## 검색 정책

Typed PPR을 `query`와 `prepare`가 공유하는 유일한 graph walk로 유지한다.

- query의 semantic/exact seed는 현재 관련성을 나타낸다.
- navigation seed는 현재 탐색 방향을 나타낸다.
- typed durable edge는 설명 가능한 multi-hop 확장을 제공한다.
- observed structural edge는 구체적인 근거로 이어진다.
- topic path는 synthetic edge가 아니라 navigation metadata로 유지한다.

`conflicts_with`에는 양방향으로 같은 가중치를 준다. 방향이 있는 semantic
관계는 선언된 방향에서 강하게, 역방향에서는 조금 약하게 유지한다. 초기
계약 이후에는 직관만으로 가중치를 조정하지 않는다. E2E 검색 corpus의
측정 결과로 조정한다.

## 구현 순서

### Phase 0 — 실패하는 계약 고정

임시 빈 데이터베이스에서 시작해 전체 수명주기를 증명하는 결정적 E2E
테스트 하나를 추가한다. 구현 전에는 빠진 Intent→Intent 단계에서 실패해야
한다.

합격 조건:

- model 없이 `setup`이 검색 가능한 관찰 그래프를 만든다.
- transcript 하나가 근거 있는 Intent node를 최소 2개 만든다.
- assistant의 번호 선택지를 사용자가 짧게 선택한 경우 선택된 내용만 Intent로
  만들고, 모호한 긍정과 응답 없는 제안은 저장하지 않는다.
- reconciliation이 예상한 Intent→Intent edge와 Intent→Material edge를
  원자적으로 만든다.
- 관련 없는 후보에는 edge를 만들지 않는다.
- 두 번째 `update` 뒤에도 두 지속 edge가 남는다.
- Material을 제거하고 복구하면 endpoint가 `missing`/`active`로 전환된다.
- 데이터베이스를 다시 연 뒤 `prepare → explain → prepare`가 열린 node를
  반복하지 않고 그래프를 따라간다.

### Phase 1 — 관계 domain 완성

상태: 구현됨.

- Intent 관계 상수 3개와 node-kind 쌍별 검증을 유지한다.
- 대칭 관계 정규화를 명시적으로 구현한다.
- 관계별 Typed PPR 방향 가중치를 지정한다.
- 허용, 거부, 중복, self-link 단위 테스트를 추가한다.

관계 이름을 위해 database migration을 만들 필요는 없다.

### Phase 2 — 검색 기반 reconciliation

상태: 구현됨.

- Pass A 뒤에 기존 Intent 후보 검색을 추가한다.
- reconciliation 후보의 USER `evidenceRefs`와 ASSISTANT `contextRefs`를 분리하고,
  순서, role, part, exact quote와 byte interval을 결정적으로 검증한다.
- reconciliation 계약과 strict JSON schema에 `intentLinks`를 추가한다.
- 후보별 호출 없이 최대 20개 후보를 한 번의 batch relation 호출로 판정한다.
- consolidation을 거쳐도 link/evidence 집합을 보존한다.
- `ReconcileMemories`가 지원하는 Intent→Material, Intent→Intent link를 모두
  받도록 한다.
- node, link, version, audit을 원자적으로 저장한다.
- commit 뒤 embedding을 동기화한다.
- 결정적인 fake model 응답으로 Phase 0을 통과시킨다.

이 단계가 핵심 연결성 결함을 해결한다.

### Phase 3 — 순서가 있는 navigation

상태: 구현됨.

- append-only navigation migration과 store method를 추가한다.
- query/explain/path와 prepare delivery action을 기록한다.
- query와 prepare가 같은 감쇠 seed 구성과 Typed PPR을 사용한다.
- `prepare`와 선택적인 Session-aware `query`가 순서 있는 trail을 사용한다.
- 반복 방문, Session 격리, 탐색 action 기록을 테스트한다.

### Phase 4 — Edge retirement

상태: 구현됨.

- 현재 edge에 state/timestamp를 추가한다.
- 인용한 명시적 수정 근거가 있을 때만 retirement를 허용한다.
- reconciliation audit에 add/retire 작업을 보존한다.
- 기본 Graph, Explain, Path, Typed PPR에서 retired edge를 제외한다.
- model 출력에서 빠진 관계를 retire하지 않고 `update`가 지속 수명주기를
  변경하지 못하는지 테스트한다.

### Phase 5 — 확장 전에 측정

상태: 수명주기 E2E 구현됨, 고정 평가 corpus와 provider usage 계측은 보류.

작은 transcript/query corpus를 저장소에 포함하고 다음을 기록한다.

- Intent 추출 precision
- Intent-link precision과 recall
- 활성 Intent orphan 비율
- 예상 path 도달 가능성
- 한 번 및 여러 번 탐색한 뒤의 retrieval recall@3
- Session 안에서 같은 결과가 반복되는 비율
- 오래되었거나 잘못 retire된 edge 수
- 관계가 없을 때의 abstention 정확도
- reconciliation model 호출 수, token, latency, retry 수
- zero-base 및 반복 update의 idempotence

이 측정이 필요성을 입증한 뒤에만 `related_to`, `supersedes`, community
detection, approximate PPR, 주기적 relinking, graph database를 검토한다.

## 반드시 지켜야 할 불변 조건

1. Project 생성은 명시적이며 observation이 Project를 만들지 않는다.
2. source code는 선택 가능한 Material 유형 하나일 뿐 core graph의 가정이 아니다.
3. zero-base update와 기본 검색에 model이 필요하지 않다.
4. USER 발언만 지속 semantic state의 권위가 된다. assistant 내용은 사용자가
   명시적으로 선택한 범위에서만 참조문으로 사용할 수 있다.
5. observed refresh가 지속 memory나 edge를 다시 쓸 수 없다.
6. 모든 지속 변경은 audit 가능하고 원자적이다.
7. 사라진 근거는 다시 연결할 수 있는 상태로 남는다.
8. 관계 없음도 유효한 결과이며 graph는 의도적으로 sparse하다.
9. navigation state는 Session 범위에만 있고 canonical graph에 속하지 않는다.
10. 열지 않은 내용은 progressive retrieval 경계를 넘지 않는다.

## 연구 근거

- [Google LangExtract](https://github.com/google/langextract)는 structured
  extraction을 정확한 source span에 정렬하고 few-shot 예시와 bounded chunk를
  사용하는 근거다. Purpory는 dependency 대신 이 grounding 계약만 차용한다.
- [HippoRAG](https://arxiv.org/abs/2405.14831)은 associative multi-hop 검색을
  위해 semantic recognition 뒤에 Personalized PageRank를 적용하는 근거다.
- [HippoRAG 2](https://arxiv.org/abs/2502.14802)는 factual retrieval을 graph
  구조로 대체하지 않고 graph retrieval과 passage 통합을 결합하는 근거다.
- [LongMemEval](https://arxiv.org/abs/2410.10813)은 extraction, multi-session
  reasoning, temporal reasoning, update, abstention을 독립적으로 테스트해야
  하는 memory 능력으로 구분한다.
- [A-MEM](https://arxiv.org/abs/2502.12110)은 새 memory를 과거 memory와
  비교하고 의미 있는 link를 동적으로 추가하는 근거다.
- [Zep](https://arxiv.org/abs/2501.13956)은 지속적으로 갱신되는 memory에서
  provenance와 과거 관계를 보존하는 근거다.
- [Microsoft GraphRAG local search](https://microsoft.github.io/graphrag/query/local_search/)는
  semantic match를 graph 진입점으로 사용한 뒤 명시적 관계로 확장하는 근거다.

이 시스템들은 설계 근거이며 dependency가 아니다. Purpory는 SQLite, 제한된
로컬 후보 집합, 명시적 사용자 권위, 기존 domain-neutral Material model을
그대로 유지한다.
