package tema2.t01;

import java.util.Scanner;

public class ej02 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Ingrese el primer numero");
        int n1=sc.nextInt();
        System.out.println("Ingrese el segundo numero");
        int n2=sc.nextInt();
        System.out.println("Ingrese el tercer numero");
        int n3=sc.nextInt();

        if (n1 <= n2 && n2 <= n3)
        {
            System.out.println("Los numeros estan ordenados");
        } else
        {
            System.out.println("No estan ordenados los numeros");
        }
    }
}
