# research-hub

소수의 연구용 머신(현재 DGX Spark 2대)에서 AI 연구 작업을 돌리는 오케스트레이터와 노드 에이전트.

설계 원칙
- **프로젝트당 작업 1개.** DB 부분 유니크 인덱스(`one_active_task_per_project`)로 강제한다.
- **상태 저장소는 Postgres 하나.** 파일·Redis에 상태를 흩지 않는다.
- **승인은 두 번뿐.** 시작 승인과 결과 승인 (P3).
- **노드는 pull 방식.** 에이전트가 허브에 접속한다. 노드에 인바운드 포트를 열지 않는다.
- **Tailscale 전용.** 허브는 tailnet 주소에만 바인딩한다. `0.0.0.0` 금지.

## 구성

| 경로 | 역할 |
|---|---|
| `hub/` | FastAPI 오케스트레이터: 에이전트 API(`/agent/v1/*`), 현황 웹(`/`), CLI |
| `agent/` | 노드 에이전트. **표준 라이브러리만** 사용. GPU·메모리·디스크, Claude/Codex 로그인, 구독 사용량 보고 |
| `migrations/` | Alembic 스키마 |
| `deploy/` | Postgres compose, systemd user 유닛, 에이전트 설치 스크립트 |

에이전트는 자격증명을 **읽기만** 한다. 토큰을 갱신하거나 다시 쓰지 않으며, 이메일·조직 ID 같은 식별 정보는 허브로 보내지 않는다.

## 허브 설치

```bash
git clone https://github.com/SeunghunLee0725/research-hub ~/research-hub && cd ~/research-hub
cp .env.example .env && chmod 600 .env        # 값 채우기
uv sync --frozen
uv run python -m hub.cli hash-password         # 결과를 HUB_ADMIN_PASSWORD_HASH에
docker compose --env-file .env -f deploy/docker-compose.yml up -d
cp deploy/systemd/research-hub.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now research-hub
```

## 머신 추가

```bash
# 허브에서
uv run python -m hub.cli add-node spark-xyz --labels gpu,claude,codex   # 토큰은 한 번만 표시
# 새 머신에서 (저장소를 ~/research-hub에 clone한 뒤)
deploy/install_agent.sh http://<hub-tailscale-ip>:8020 <token>
```

## 프로젝트 등록

```bash
uv run python -m hub.cli add-project plasma-kg "플라즈마 KG" /path/to/workdir --node-labels gpu
```

## 테스트

```bash
docker run -d --name research-hub-pg-test -e POSTGRES_DB=hub_test -e POSTGRES_USER=hub \
  -e POSTGRES_PASSWORD=hub -p 127.0.0.1:5441:5432 postgres:16-alpine
uv run pytest --cov
```

## 로드맵

- [x] P1 뼈대: 스키마, 에이전트 heartbeat·로그인·사용량, 현황판
- [x] P2 실행: 작업·단계 실행, 긴 작업 추적, 텔레그램 알림
- [x] P3 승인: 시작·결과 승인, 결과 카드
- [x] P4 분업: Claude/Codex 단계별 라우팅, 교차 검증
- [ ] P5 이전: 기존 자율 루프 정리
