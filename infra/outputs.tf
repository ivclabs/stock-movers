# outputs.tf - printed at the end of `terraform apply`

output "website_url" {
  description = "Open this in your browser"
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "bucket" {
  value = aws_s3_bucket.site.id
}

output "pipeline_function" {
  value = aws_lambda_function.pipeline.function_name
}
