from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """

        ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "active_generation_id" VARCHAR(128);
CREATE TABLE IF NOT EXISTS "knowledge_generations" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "generation_id" VARCHAR(128) NOT NULL,
    "knowledge_version" INT NOT NULL,
    "manifest_hash" VARCHAR(64) NOT NULL,
    "mode" VARCHAR(16) NOT NULL,
    "base_generation_id" VARCHAR(128),
    "published_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "created_by_id" INT REFERENCES "users" ("id") ON DELETE SET NULL,
    "project_id" INT NOT NULL REFERENCES "projects" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_knowledge_g_project_534b4e" UNIQUE ("project_id", "generation_id")
);
CREATE INDEX IF NOT EXISTS "idx_knowledge_g_project_f9e128" ON "knowledge_generations" ("project_id", "knowledge_version");
COMMENT ON COLUMN "knowledge_generations"."mode" IS 'APPEND: append\nREPLACE: replace';
CREATE TABLE IF NOT EXISTS "llm_attempts" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "project_id" INT NOT NULL,
    "request_id" VARCHAR(128) NOT NULL,
    "attempt_id" VARCHAR(128) NOT NULL UNIQUE,
    "outcome" VARCHAR(32) NOT NULL,
    "error_code" VARCHAR(32),
    "usage_status" VARCHAR(16) NOT NULL,
    "input_tokens" INT,
    "output_tokens" INT,
    "cached_input_tokens" INT,
    "reasoning_output_tokens" INT,
    "cost" NUMERIC(18,8),
    "cost_status" VARCHAR(16),
    "currency" VARCHAR(3),
    "rate_version" VARCHAR(128),
    "priced_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_llm_attem_project_26b3c5" ON "llm_attempts" ("project_id", "created_at");
CREATE INDEX IF NOT EXISTS "idx_llm_attem_request_60e83a" ON "llm_attempts" ("request_id");
"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """

        DROP TABLE IF EXISTS "llm_attempts";
DROP TABLE IF EXISTS "knowledge_generations";
ALTER TABLE "projects" DROP COLUMN IF EXISTS "active_generation_id";
"""


MODELS_STATE = (
    "eJztXV1zmzgX/isMV9mZbMaxncSb98pxaNZTx846zrazTYeRseyw4cPlo0lmp//9lYQwAg"
    "QBajuE6oY4ko4Qj4R0zqNzxH+yaS+g4R5d2trdegE8KJ9L/8kWMPGPdOahJIP1OsrCCR6Y"
    "G6T0wtZUn5Qj6WDueg7QPJS1BIYLUdICupqjrz3dtlCq5RvGIRFDBXVrFSX5lv7Nh6pnr6"
    "D3AB2U8eUrStatBXxGldN/14/qUofGItZkfYHvTdJV72VN0oaW94EUxHebq5pt+KYVFV6/"
    "eA+2tSmtWx5OXUELOuhZcPWe4+Pm49bRhw2fKGhpVCRoIiOzgEvgGx7zuHM1SpNVdTyZqb"
    "fKTFXlEgBptoXBRU11ydOvcBN+bx93z7q9zmm3h4qQZm5Szn4Et46ACQQJPOOZ/IPkAw8E"
    "JQjG8XGQhnXwABzF8k2C7RA1CFgaTGEcyiZQRs1PohximgdzmBDhHI2t6kDL/ZsbZXx5Lq"
    "HBDa3FvTVVbkb9gXIuOXBtAPpUr3eDCZ5VA1or7wH9e3yag/nf/engz/704Pj0N1y3jV6U"
    "4CUa05w2ycLdEnWD65smcF7SPTGDzxkjnBGpcwcUHuk5kM6UzzNcs+m63wwWyoPr/meCsv"
    "lCc0aT8VVYnIF+MJpcJCDXHIjBUYGXRv0S5Xi6CfnIxyUT4C+o6FH44x12hYwecDGxjBc6"
    "2eV1zfBauZ31r29i/XPZnyk4px3rmzD1IPlmbCqRPg1nf0r4X+mfyVgh8Nqut3LIHaNys3"
    "9k3Cbge7Zq2U8qWDDzcpgaosbt9fmLWmo9Scm9vrS8h47eyuoSobt27H+h5pWDNi4kcKW4"
    "Yj1o+chdtCliaYw/2A7UV9ZH+JJauRO4Uv3vJqrpnUH8IxxGYWo0ATjgaaM+JkYXQgA9N/"
    "QCLad/O+hfKnLGBLEFeO9c+I6X4lexTU2KfHjxSJ4D7fEJOAs1NqRxjt22Eymbsukss20m"
    "U4AFVgQi/Cy45RT7K8f210iR9WSO3RNlHubZPStcTNVQOWH3NMvuwX3KXaYu9FW2EhAJbW"
    "eZqgPEf7Tbnc5Zu9U57Z10z85Oeq0N1umsPNAvhlcY95hml1YR5rZvkRnDQxPFygFm6V7I"
    "qqFJmsOeO0XYQtkd0lxbSGjrQluvmUZZRFt/G3VyaH3X+Rw6zclVJHVSZvdK5Bd2tMHntY"
    "5mCzw3fxXa5V61y0xWPWMV3SqT/rbQxpjy024Bpvy0m8mU46y4qsIM6pKqSlyygarKO1FN"
    "QkxydRMHfrcfK2mkccktdDN9e0Qvb7+Xhd2RPY821+4QezBiD+Z94CqsOrEHU3Nsa70H8x"
    "EtAQZcrOBVAH6AQcqC5hU7zDOnH0MBdbWRKGZcy/d+q3sM8bWzxNduh/w+Jtc5ufbIFZBr"
    "ULJNri1yXTBXktJdSlErjiSc1CbVdYlYt8VUFFTak5g6TiSmGV3mRvOgcnKFTH3H9KbMM7"
    "TYas+YOrTgFrHbIXj07/BITgxDgQyLDIdqiXEr0XPh1+5rHg0TDdbv0HHxMBRszF7ZmHhf"
    "pfDNpmVSgo1wtEv4MrZ7RZwZ271sb0acF9dB00O++KjmygqNlKPpo2VXX0LXUx+A+1BmXK"
    "cEGziut888ClfpKtPL1l2l58Bl1b6SUzpfulK31IwX3MO0vvbnhu4+VCLskrINpOzeCUUn"
    "mFnBzNaSma3bhCqI2driKojZX4aYrdmssB1eFrVCGt+NRm9FzI4Ms+950FxzveOZ3MM8Gt"
    "YwTLTMk4Jl2NdlRPt1GTqQpiyTXGI34B7/YNjALmHyTqiwxNCIXUYaMnIdhtikZGYrqinG"
    "A3KEl8kqgjbRdlPq8n9p7jKfcaSPekKfgpAejjQcj38/bmfQswK6gtCVcJWj7yejt6KSX5"
    "CWhypwvQ29K/jaffG1QlHajQLKjOgUrtmMSVyqzgRWnagSuiyWxDou1Thv0J0gbfueZpul"
    "3G0ZkQaO5067AMiddibGOCvhc+s4tqOWdWqOSzWPYt0+zr6LtHUV2U2e71bdaUjWUecBLk"
    "+Vm8l0plySLQbbQe25t5Tb2fC6TxLRsqObgKTejT+OJ5/G55Jv4Q1Cqx57Ebq19j2kZD5C"
    "i9Nh2YpgQkywZWllBc3RlbBNyQlwOfGeQMM7MRWHb4a0AJqncgPXttAd1arjOacGAThnZN"
    "sub8cKamghMbKCr1zuVlUgc0Rl3x+0ObhdKgO0xo4OjnuHga7tfjOC6MbNOthtJRc7jNNP"
    "KieJKmqsE3JVk6kymIwHw1GQiuDWdCNLZamHgqL5jgMtjUPX5wQjMjI17qGqWnsRpT1bZ0"
    "8CjGHIdnLLYVUScs0DejceKI6uVXM/YQVFUKBwPRGuJ2/vepLaxX+bzdBraM7RTPygr2XO"
    "ZiiTe5i3GWpuyu3gmIcvsk93y0P3htyQA8dGbRK7VnvdtSKYV9SLQ9lak3X9y+vh+FwCC1"
    "O37q27W2V6LoWjsgaarlg1fpFVQ/jU7WpyY3cioFMOVEZCIPq6l2I4cYrg5jwnOmZQve6c"
    "KBw/m3DOWog9RxNnuiVbDadPJs7rbZZ2TcKHtJLuNDGhxnnTFKHXssm1FLVG/pYANyxfZ5"
    "ulIrLbjx1dA9d9stHcWDZUNyXYQLjbJ0WsQVQqE3CSF0ccN6JC9HlSbH+6bKvWMzPj2UgO"
    "yagegZslL3ZACu2ACJ4jG/3m8hyk7+ySJnlcSFjlr1vlFDFhmBeKbrO5tvkUjfXpcDDLMy"
    "BjyzT7Zck46hdU+MPHKTQ2x4LxAY99ybIhqMemgMS3aKojFfv2TRORYg5br45SdKp7EyHK"
    "PECvOmAZh/g1Eb3EVm91zOJ7y02EisSO/fzg+otW0ySY9sCiXgDueZtM7mEBLlWdg1Kna7"
    "Ihv0Gwr8ZE9KbOjaShwCds+dyTH4Pg5c4iiBMOzqQ8Sx/6CJlqAXOj1rmUip9uRXI0ZvqE"
    "c4okuXZScdC9VBx0L4hRpg1kA6FbqQayT0wr0YKnD5KO+WiGd+VETrP4a8zvDhMFXiDAXP"
    "RdrfuukpeQ2LTY56aFAXDcNFEky1nuaUFhvYvP1pWCurlcmPD5ET4/9UVU+PwIn596Altf"
    "n5+Ndc+xVVnLP9tSjdEM+/vCIg1lFJ72e1WrgfuIpojK34/mijdp/dr3x6Nr8Sn1XxJ6E7"
    "rkuJey6MflRAdU7gAPPnOUjxlK5QMflm+EF1eeTal8nsXMydCn5eC6//m3mEk5moyvwuKM"
    "D8xgNLlInrZj4jG71A3+gM92OUoJNs/XaCc+dD932MNbnEEl4y9XYNTKgS7jD18Mx1fnEp"
    "W/t/rj20/KFB/pACz3CTp1OdEhaE2ZOSeSaMKw3/ekIzjG7KHeXI4xfOcrdHtCVJw1Ueez"
    "JjadVfaLF2lBcRqX+OTFGxHLggXd3ScvmBd9f9x9zaaFotimJ8VaffSCgM+hmMNOyaaX8Z"
    "6EiChtFotcmT/eCXNcB6j3zJ3hl6ps0Ckr0wRrdtdxp0t0V7UsyDGhRjCVe+DKglMzPbWS"
    "yssXFhaFcPn6demYlL1TJJxMRGbIRSMzwveBPTClOl7NMxx3EJ/x4//CXMEE"
)
