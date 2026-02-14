FROM public.ecr.aws/lambda/python:3.12

COPY src/ ${LAMBDA_TASK_ROOT}/src/

CMD ["src.handler.lambda_handler"]
