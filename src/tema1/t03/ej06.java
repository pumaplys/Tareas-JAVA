package tema1.t03;

import java.util.Scanner;

public class ej06 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Digite um numero: ");
        double num1 = sc.nextDouble();
        System.out.println("Digite um numero: ");
        double num2 = sc.nextDouble();

        System.out.println((num2 != 0) ? "El resultado de la division es: " + (num1/num2) : "No se puede dividir entre cero");


    }


}
