package tema2.t01;

import java.util.Scanner;

// Pedir un número entre 0 y 9.999, decir si es capicúa.
public class ej24 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.print("Introduce un número entre 0 y 9999: ");
        int numero = sc.nextInt();

        if (numero < 0 || numero > 9999) {
            System.out.println("Número fuera de rango.");
        } else {
            int original = numero;
            int invertido = 0;

            while (numero > 0) {
                int digito = numero % 10;      // obtener el último dígito
                invertido = invertido * 10 + digito; // construir el número invertido
                numero = numero / 10;          // eliminar el último dígito
            }

            if (original == invertido) {
                System.out.println("El número es capicúa.");
            } else {
                System.out.println("El número no es capicúa.");
            }
        }
    }
}
