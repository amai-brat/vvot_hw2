openapi: 3.0.0
info:
  title: ${api_name}
  version: 1.0.0
paths:
  /api/tasks:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${fetch_ydb_function_id}
        service_account_id: ${service_account_id}
        timeout_ms: 30000
      responses:
        '200':
          description: Success
          content:
            application/json:
              schema:
                type: object
                properties:
                  tasks:
                    type: array
                    items:
                      type: object
  /:
    get:
      x-yc-apigateway-integration:
        type: object_storage
        bucket: ${bucket_name}
        object: ${index_object_key}
        service_account_id: ${service_account_id}
    post:
      x-yc-apigateway-integration:
        payload_format_version: '2.0'
        function_id: ${form_receiver_function_id}
        tag: ''
        type: cloud_functions
        service_account_id: ${service_account_id}
  /tasks:
    get:
      x-yc-apigateway-integration:
        type: object_storage
        bucket: ${bucket_name}
        object: ${tasks_object_key}
        service_account_id: ${service_account_id}
  /pdf/{file+}:
    get:
      parameters:
        - in: path
          name: file
          schema:
            type: string
          required: true
      x-yc-apigateway-integration:
        bucket: ${bucket_name}
        type: object_storage
        service_account_id: ${service_account_id}
        object: pdf/{file}
x-yc-apigateway:
  cors:
    origin:
      - "*"
    methods:
      - GET
      - POST
      - PUT
      - DELETE
      - OPTIONS
    allowHeaders:
      - "*"